"""Background PDF-compile jobs for the web app's upload flow.

`coursec build` already runs the pipeline from the terminal; this module
runs the same sequence of pass calls from a background thread instead, so
`POST /api/build` can accept an uploaded PDF and return immediately while
the compile runs. It deliberately mirrors `cli.py`'s `build()` pass by pass
rather than sharing one function with it — the same choice `demo/harness.py`
already makes, and for the same reason: a web job and a CLI invocation
report progress, handle a mid-pipeline failure, and choose their output
paths differently enough that a shared "do everything" function would need
as many parameters as it saved lines.

A build's own `DiagnosticSink` never survives past this module (same fact
`web/api.py`'s docstring states about persisted builds) — `job.log` is the
record of what happened, kept only in memory, only for this process's
lifetime.
"""

from __future__ import annotations

import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from coursec.core.backend_registry import LLMBackend
from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.emit.blueprint import Blueprint
from coursec.emit.targets import BlueprintInfeasible, render_all_targets
from coursec.passes import compose as compose_pass
from coursec.passes import evidence as evidence_pass
from coursec.passes import gap as gap_pass
from coursec.passes import item_gates, origin_linter
from coursec.passes import pilot as pilot_pass
from coursec.passes import structure as structure_pass
from coursec.passes import syllabus as syllabus_pass
from coursec.passes import understand as understand_pass
from coursec.passes import verify as verify_pass
from coursec.passes.ingest import block_type_histogram, ingest_pdf

JOBS_ROOT = Path(tempfile.gettempdir()) / "coursec-web-builds"

TARGET_NAMES = ("booklet", "cheat_sheet", "question_paper", "answer_key")


@dataclass
class BuildJob:
    id: str
    filename: str
    backend_name: str
    status: str = "running"  # "running" | "succeeded" | "failed"
    error: str | None = None
    emitted: bool = False  # the 4 PDFs were actually written
    log: list[str] = field(default_factory=list)
    db_path: Path | None = None
    work_dir: Path | None = None
    targets: dict[str, Path] = field(default_factory=dict)

    def append(self, message: str) -> None:
        self.log.append(message)


_jobs: dict[str, BuildJob] = {}
_jobs_lock = threading.Lock()


def get_job(job_id: str) -> BuildJob | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def start_build(
    pdf_bytes: bytes, filename: str, *, backend: LLMBackend, backend_name: str
) -> BuildJob:
    """Writes the upload to a per-job directory and starts the pipeline on
    a background thread. Returns immediately with the job in "running"
    state — `get_job(job.id)` is how a caller (or a test) observes it."""
    job_id = uuid.uuid4().hex
    work_dir = JOBS_ROOT / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = work_dir / (filename or "chapter.pdf")
    pdf_path.write_bytes(pdf_bytes)

    job = BuildJob(id=job_id, filename=filename, backend_name=backend_name, work_dir=work_dir)
    with _jobs_lock:
        _jobs[job_id] = job

    threading.Thread(target=_run, args=(job, pdf_path, backend), daemon=True).start()
    return job


def _default_blueprint(accepted_items: list) -> Blueprint:
    """Same sizing rule as `cli.py`'s `_default_blueprint` — a blueprint
    the actual generated items can satisfy, so a typical upload is
    feasible by construction."""
    bloom_levels = sorted({item.bloom_level for item in accepted_items})
    if not bloom_levels:
        return Blueprint(total_marks=0)
    share = 1.0 / len(bloom_levels)
    return Blueprint(
        total_marks=min(len(accepted_items), 10),
        marks_per_item=1,
        bloom_mix={level: share for level in bloom_levels},
    )


def _run(job: BuildJob, pdf_path: Path, backend: LLMBackend) -> None:
    sink = DiagnosticSink()
    assert job.work_dir is not None
    db_path = job.work_dir / "coursec.db"

    try:
        with Graph(db_path) as graph:
            blocks = ingest_pdf(pdf_path, graph, sink)
            histogram = block_type_histogram(blocks)
            job.append(f"ingest: {len(blocks)} blocks from {pdf_path.name}")
            for block_type, count in sorted(histogram.items()):
                job.append(f"  {block_type}: {count}")
            job.db_path = db_path  # queryable from here on, even if a later stage fails

            try:
                concepts, section_by_concept = understand_pass.understand(
                    blocks, graph, sink, backend=backend
                )
            except RuntimeError as exc:
                job.status, job.error = "failed", f"understand: {exc}"
                job.append(f"understand: FAILED — {exc}")
                return

            syllabus_nodes = syllabus_pass.load_syllabus(graph)
            links = syllabus_pass.link_concepts_to_syllabus(graph, concepts, syllabus_nodes)
            gaps = syllabus_pass.coverage_report(syllabus_nodes, links)
            linked = sum(1 for link in links if link.syllabus_node_id)
            link_rate = linked / len(links) if links else 0.0
            job.append(
                f"understand: {len(concepts)} concepts "
                f"({link_rate:.0%} linked, {len(gaps)} coverage gaps)"
            )

            try:
                prerequisite_edges, _part_of = structure_pass.structure(
                    graph, concepts, section_by_concept, sink, backend=backend
                )
            except RuntimeError as exc:
                job.status, job.error = "failed", f"structure: {exc}"
                job.append(f"structure: FAILED — {exc}")
                return
            job.append(f"structure: {len(prerequisite_edges)} prerequisite edges")

            gap_vectors = gap_pass.compute_gap_vectors(graph, concepts)
            budgets = gap_pass.allocate_budgets(gap_vectors, global_cap=40)
            try:
                evidence_pass.retrieve_evidence(
                    graph, concepts, gap_vectors, budgets, sink,
                    search=evidence_pass.no_search_backend,
                )
            except RuntimeError as exc:
                job.status, job.error = "failed", f"evidence: {exc}"
                job.append(f"evidence: FAILED — {exc}")
                return
            job.append("gap + evidence: budget allocated, retrieval attempted")

            try:
                lesson_blocks = []
                for concept in concepts:
                    lesson_blocks.extend(
                        compose_pass.compose_concept(graph, concept, sink, backend=backend)
                    )
            except RuntimeError as exc:
                job.status, job.error = "failed", f"compose: {exc}"
                job.append(f"compose: FAILED — {exc}")
                return
            job.append(f"compose: {len(lesson_blocks)} lesson blocks")

            try:
                numeric_origin_violations = 0
                for block in lesson_blocks:
                    verify_pass.verify_lesson_block(graph, block, sink, backend=backend)
                    verify_pass.run_compute_check(graph, block, sink)
                    numeric_origin_violations += origin_linter.lint_lesson_block(
                        graph, block, sink
                    )
            except RuntimeError as exc:
                job.status, job.error = "failed", f"verify: {exc}"
                job.append(f"verify: FAILED — {exc}")
                return
            unsupported = sum(1 for d in sink.all() if d.code == "sentence_dropped_unsupported")
            contradicted = sum(
                1 for d in sink.all() if d.code == "sentence_dropped_contradicted"
            )
            job.append(
                f"verify: {unsupported} unsupported, {contradicted} contradicted, "
                f"{numeric_origin_violations} numeric-origin violations"
            )

            try:
                accepted_items = []
                total_rejected = 0
                for concept in concepts:
                    accepted, rejected = item_gates.assess_concept(
                        graph, concept, sink, backend=backend
                    )
                    accepted_items.extend(accepted)
                    total_rejected += rejected
            except RuntimeError as exc:
                job.status, job.error = "failed", f"assess: {exc}"
                job.append(f"assess: FAILED — {exc}")
                return
            job.append(f"assess: {len(accepted_items)} accepted, {total_rejected} rejected")

            item_stats = pilot_pass.run_pilot(graph, accepted_items, sink, seed=0)
            quarantined = sum(1 for s in item_stats if s.quarantined)
            job.append(
                f"pilot (screening, n={pilot_pass.COHORT_SIZE}): "
                f"{len(item_stats)} fit, {quarantined} quarantined"
            )

            if sink.has_errors():
                job.append(
                    "emit: an error-severity diagnostic is present — producing no PDF "
                    "(see the certificate for which invariant broke)"
                )
            else:
                blueprint = _default_blueprint(accepted_items)
                try:
                    rendered = render_all_targets(
                        graph, concepts, blueprint,
                        work_dir=job.work_dir / "emit", pdf_path=pdf_path,
                    )
                except BlueprintInfeasible as exc:
                    job.append(f"emit: question paper/answer key skipped — {exc}")
                else:
                    emit_dir = job.work_dir / "emit"
                    emit_dir.mkdir(exist_ok=True)
                    for name in TARGET_NAMES:
                        data = getattr(rendered, name)
                        path = emit_dir / f"{name}.pdf"
                        path.write_bytes(data)
                        job.targets[name] = path
                    job.emitted = True
                    job.append(f"emit: wrote {len(job.targets)} PDFs")

        for diagnostic in sink.all():
            job.append(f"  [{diagnostic.severity}] {diagnostic.code}: {diagnostic.message}")
        job.status = "succeeded"

    except Exception as exc:  # noqa: BLE001 — a background thread's only chance to report
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        job.append(f"FAILED — {job.error}")
