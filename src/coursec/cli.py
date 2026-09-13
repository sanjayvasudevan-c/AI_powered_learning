"""coursec CLI.

D0 shipped three stubs. D1 gave `build` its first real stage (ingest); D2
adds understand (concept extraction + syllabus anchoring) and structure
(prerequisite DAG). `build` runs the pipeline built so far and exits
non-zero only if it fails. D7 gives `quiz` and `serve` their real
implementations, both over `passes/learn.py`: `quiz` is a terminal-driven
adaptive session, `serve` shells out to `streamlit run` on `ui/quiz_app.py`.
"""

from __future__ import annotations

import os
import string
import subprocess
import sys
from pathlib import Path

import typer

from coursec.core.anthropic_backend import anthropic_backend
from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.emit import certificate as certificate_module
from coursec.emit.blueprint import Blueprint
from coursec.emit.targets import BlueprintInfeasible, render_all_targets
from coursec.passes import compose as compose_pass
from coursec.passes import evidence as evidence_pass
from coursec.passes import gap as gap_pass
from coursec.passes import item_gates, origin_linter
from coursec.passes import learn as learn_pass
from coursec.passes import pilot as pilot_pass
from coursec.passes import structure as structure_pass
from coursec.passes import syllabus as syllabus_pass
from coursec.passes import understand as understand_pass
from coursec.passes import verify as verify_pass
from coursec.passes.ingest import block_type_histogram, ingest_pdf
from coursec.viz.graph_html import render_graph_html

app = typer.Typer(no_args_is_help=True, add_completion=False)

BUILD_DB_PATH = Path("build/coursec.db")
GRAPH_HTML_PATH = Path("build/graph.html")
CERTIFICATE_PATH = Path("build/certificate.html")
EMIT_WORK_DIR = Path("build/emit")


def _default_blueprint(accepted_items: list) -> Blueprint:
    """A blueprint sized to what was actually generated, so a typical run
    is feasible by construction — an explicit, narrower blueprint is a
    caller concern (see emit.blueprint.Blueprint), not the CLI's default."""
    bloom_levels = sorted({item.bloom_level for item in accepted_items})
    if not bloom_levels:
        return Blueprint(total_marks=0)
    share = 1.0 / len(bloom_levels)
    return Blueprint(
        total_marks=min(len(accepted_items), 10),
        marks_per_item=1,
        bloom_mix={level: share for level in bloom_levels},
    )


@app.command()
def build(pdf: Path = typer.Argument(..., help="Source chapter PDF to compile.")) -> None:
    """Compile a source PDF into the Concept Graph and emit targets.

    Runs the full pipeline: ingest -> understand -> structure -> gap ->
    evidence -> compose -> verify -> assess -> emit. Once this has produced
    a `coursec.db`, `quiz`/`serve` run D7's adaptive quiz over it. D8's demo
    harness isn't built yet.
    """
    if not pdf.exists():
        typer.echo(f"coursec build: no such file: {pdf}", err=True)
        raise typer.Exit(code=1)

    BUILD_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    BUILD_DB_PATH.unlink(missing_ok=True)
    sink = DiagnosticSink()

    with Graph(BUILD_DB_PATH) as graph:
        blocks = ingest_pdf(pdf, graph, sink)

        histogram = block_type_histogram(blocks)
        typer.echo(f"ingest: {len(blocks)} blocks from {pdf}")
        for block_type, count in sorted(histogram.items()):
            typer.echo(f"  {block_type}: {count}")

        try:
            concepts, section_by_concept = understand_pass.understand(
                blocks, graph, sink, backend=anthropic_backend
            )
        except RuntimeError as exc:
            typer.echo(f"understand: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        syllabus_nodes = syllabus_pass.load_syllabus(graph)
        links = syllabus_pass.link_concepts_to_syllabus(graph, concepts, syllabus_nodes)
        gaps = syllabus_pass.coverage_report(syllabus_nodes, links)
        link_rate = sum(1 for link in links if link.syllabus_node_id) / len(links) if links else 0.0
        abstain_rate = 1.0 - link_rate

        typer.echo(f"understand: {len(concepts)} concepts")
        typer.echo(f"  link rate: {link_rate:.0%}")
        typer.echo(f"  abstain rate: {abstain_rate:.0%}")
        typer.echo(f"  coverage gaps: {len(gaps)}")

        try:
            prerequisite_edges, part_of_edges = structure_pass.structure(
                graph, concepts, section_by_concept, sink, backend=anthropic_backend
            )
        except RuntimeError as exc:
            typer.echo(f"structure: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        typer.echo(f"structure: {len(prerequisite_edges)} prerequisite edges")

        render_graph_html(concepts, prerequisite_edges, GRAPH_HTML_PATH)
        typer.echo(f"wrote {GRAPH_HTML_PATH}")

        gap_vectors = gap_pass.compute_gap_vectors(graph, concepts)
        histogram = gap_pass.gap_histogram(gap_vectors)
        typer.echo("gap histogram:")
        for dimension, count in histogram.items():
            typer.echo(f"  {dimension}: {count}")

        budgets = gap_pass.allocate_budgets(gap_vectors, global_cap=40)
        try:
            evidence_pass.retrieve_evidence(
                graph, concepts, gap_vectors, budgets, sink,
                search=evidence_pass.no_search_backend,
            )
        except RuntimeError as exc:
            typer.echo(f"evidence: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        try:
            lesson_blocks = []
            for concept in concepts:
                lesson_blocks.extend(
                    compose_pass.compose_concept(graph, concept, sink, backend=anthropic_backend)
                )
        except RuntimeError as exc:
            typer.echo(f"compose: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        try:
            numeric_origin_violations = 0
            for block in lesson_blocks:
                verify_pass.verify_lesson_block(graph, block, sink, backend=anthropic_backend)
                verify_pass.run_compute_check(graph, block, sink)
                numeric_origin_violations += origin_linter.lint_lesson_block(graph, block, sink)
        except RuntimeError as exc:
            typer.echo(f"verify: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        unsupported_count = sum(
            1 for d in sink.all() if d.code == "sentence_dropped_unsupported"
        )
        contradicted_count = sum(
            1 for d in sink.all() if d.code == "sentence_dropped_contradicted"
        )
        quarantine_count = sum(1 for block in lesson_blocks if block.status == "quarantined")

        typer.echo(f"compose: {len(lesson_blocks)} lesson blocks")
        typer.echo("verify:")
        typer.echo(f"  unsupported sentences dropped: {unsupported_count}")
        typer.echo(f"  contradicted sentences dropped: {contradicted_count}")
        typer.echo(f"  numeric-origin violations: {numeric_origin_violations}")
        typer.echo(f"  quarantined slots: {quarantine_count}")

        try:
            accepted_items = []
            total_rejected = 0
            for concept in concepts:
                accepted, rejected = item_gates.assess_concept(
                    graph, concept, sink, backend=anthropic_backend
                )
                accepted_items.extend(accepted)
                total_rejected += rejected
        except RuntimeError as exc:
            typer.echo(f"assess: {exc}", err=True)
            raise typer.Exit(code=1) from exc

        total_generated = len(accepted_items) + total_rejected
        typer.echo(f"assess: {total_generated} items generated, {len(accepted_items)} accepted")
        typer.echo(f"  rejections: {total_rejected}")

        item_stats = pilot_pass.run_pilot(graph, accepted_items, sink, seed=0)
        quarantined_items = sum(1 for s in item_stats if s.quarantined)
        typer.echo(
            f"pilot (SCREENING, n={pilot_pass.COHORT_SIZE}, not a calibration claim): "
            f"{len(item_stats)} items fit, {quarantined_items} quarantined"
        )
        if item_stats:
            discriminations = [s.discrimination for s in item_stats]
            difficulties = [s.difficulty for s in item_stats]
            typer.echo(
                f"  discrimination: min={min(discriminations):.2f} max={max(discriminations):.2f}"
            )
            typer.echo(f"  difficulty: min={min(difficulties):.2f} max={max(difficulties):.2f}")

        certificate_data = certificate_module.build_certificate_data(
            graph, concepts, sink.all(), syllabus_nodes=syllabus_nodes, links=links
        )
        certificate_module.write_certificate(CERTIFICATE_PATH, certificate_data)
        typer.echo(f"wrote {CERTIFICATE_PATH}")

        if sink.has_errors():
            typer.echo(
                "emit: an error-severity diagnostic is present — producing no PDF at all",
                err=True,
            )
        else:
            blueprint = _default_blueprint(accepted_items)
            try:
                rendered = render_all_targets(
                    graph, concepts, blueprint, work_dir=EMIT_WORK_DIR, pdf_path=pdf
                )
            except BlueprintInfeasible as exc:
                typer.echo(f"emit: question paper/answer key skipped — {exc}", err=True)
            else:
                (EMIT_WORK_DIR / "booklet.pdf").write_bytes(rendered.booklet)
                (EMIT_WORK_DIR / "cheat_sheet.pdf").write_bytes(rendered.cheat_sheet)
                (EMIT_WORK_DIR / "question_paper.pdf").write_bytes(rendered.question_paper)
                (EMIT_WORK_DIR / "answer_key.pdf").write_bytes(rendered.answer_key)
                typer.echo(f"emit: wrote 4 PDFs to {EMIT_WORK_DIR}")

    for diagnostic in sink.all():
        typer.echo(f"  [{diagnostic.severity}] {diagnostic.code}: {diagnostic.message}", err=True)

    if sink.has_errors():
        raise typer.Exit(code=1)


@app.command()
def serve(
    db: Path = typer.Argument(..., help="Path to a built coursec.db (from `coursec build`)."),
    student_id: str = typer.Option(
        "student", help="Identifies whose mastery this session updates."
    ),
    port: int = typer.Option(8501, help="Port to serve the Streamlit app on."),
) -> None:
    """Serve the Streamlit adaptive-quiz UI (ui/quiz_app.py) over `db`."""
    if not db.exists():
        typer.echo(f"coursec serve: no such database: {db}", err=True)
        raise typer.Exit(code=1)

    app_path = Path(__file__).parent / "ui" / "quiz_app.py"
    env = {**os.environ, "COURSEC_DB_PATH": str(db.resolve()), "COURSEC_STUDENT_ID": student_id}
    result = subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(app_path), "--server.port", str(port)],
        env=env,
    )
    raise typer.Exit(code=result.returncode)


@app.command()
def quiz(
    db: Path = typer.Argument(..., help="Path to a built coursec.db (from `coursec build`)."),
    student_id: str = typer.Option(
        "student", help="Identifies whose mastery this session updates."
    ),
    max_questions: int = typer.Option(10, help="Stop after this many questions."),
) -> None:
    """Run an adaptive quiz session from the command line.

    Each question is the accepted `Item` on the concept this student's BKT
    mastery is currently weakest on (`learn.select_next_item`); every
    answer updates that concept's `Mastery` (`learn.record_response`), and
    a wrong answer is followed by `learn.diagnose_root_cause`'s readout —
    which prerequisite, if any, is the deeper thing actually worth
    reviewing.
    """
    if not db.exists():
        typer.echo(f"coursec quiz: no such database: {db}", err=True)
        raise typer.Exit(code=1)

    sink = DiagnosticSink()
    asked: frozenset[str] = frozenset()
    questions_asked = 0

    with Graph(db) as graph:
        while questions_asked < max_questions:
            item = learn_pass.select_next_item(graph, student_id, asked_item_ids=asked)
            if item is None:
                break
            asked = asked | {item.id}
            questions_asked += 1

            concept = graph.get(item.concept_id)
            typer.echo(f"\nQ{questions_asked}. [{concept.name}] {item.stem}")

            if item.item_type == "mcq":
                options = learn_pass.mcq_options(item)
                letters = string.ascii_uppercase[: len(options)]
                for letter, (text, _) in zip(letters, options, strict=True):
                    typer.echo(f"  {letter}) {text}")
                response = typer.prompt("Answer").strip().upper()
                correct = response in letters and options[letters.index(response)][1]
            else:
                response = typer.prompt("Answer").strip()
                correct = response.lower() == item.key.strip().lower()

            learn_pass.record_response(
                graph, sink, student_id=student_id, concept_id=item.concept_id, correct=correct
            )

            if correct:
                typer.echo("Correct.")
            else:
                typer.echo(f"Incorrect. The answer was: {item.key}")
                root = learn_pass.diagnose_root_cause(graph, item.concept_id, student_id)
                if root.concept_id != item.concept_id:
                    root_concept = graph.get(root.concept_id)
                    typer.echo(
                        f"  This likely traces back to '{root_concept.name}' "
                        f"({root.depth} prerequisite level(s) back) — review that first."
                    )

    typer.echo(f"\nSession complete: {questions_asked} question(s) answered.")
    if questions_asked == 0:
        typer.echo("coursec quiz: no accepted items in this graph", err=True)
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
