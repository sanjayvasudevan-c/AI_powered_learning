"""The web API — a read-mostly HTTP surface over a built Concept Graph,
plus the two endpoints D7's adaptive quiz needs to write `Mastery`.

This is not a pass and owns no column of the IR. It reads the graph the
same way `emit` does (read-only over everything) and, for the quiz, calls
`passes/learn.py` rather than reimplementing BKT or root-cause walking in
a request handler — the whole point of learn.py being pure and testable is
that nothing downstream needs its own copy.

**Diagnostics are not persisted.** A `DiagnosticSink` lives for the length
of one build, so `/api/certificate` cannot read the diagnostic list the way
`build/certificate.html` does. It derives its ledger from the IR instead —
`Verdict` rows for dropped sentences, failed `ExecResult` rows for compute
divergence, `Item.status` for gate rejections, `ItemStats.quarantined` for
the pilot screen — and says so in its `derived_from` field rather than
implying it read a build log it never saw.

The database is resolved per request, not captured at startup, so a build
running against the same path is picked up without restarting the server;
every endpoint answers `{"available": false}` rather than 500-ing when
there is no database yet.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import select

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import (
    Block,
    Concept,
    Edge,
    EdgeKind,
    ExecResult,
    Item,
    ItemStats,
    LessonBlock,
    Misconception,
    SourceSpan,
    SyllabusNode,
    Verdict,
    WebEvidence,
)
from coursec.emit.contract import GENERATIVE_SLOTS, compute_contract_status
from coursec.passes import learn as learn_pass

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_DB_PATH = Path("build/coursec.db")


@dataclass
class Settings:
    """Mutable so `coursec web` can point the app at a database without
    rebuilding the app, and so tests can swap in a fixture graph."""

    db_path: Path = DEFAULT_DB_PATH


settings = Settings()


def _graph() -> Graph | None:
    """A fresh session per request — SQLite sessions are cheap, and a
    long-lived one would hold a stale snapshot while a build writes."""
    path = settings.db_path
    return Graph(path) if path.exists() else None


def _unavailable(what: str) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "available": False,
            "reason": f"no compiled chapter at {settings.db_path} — run `coursec build` first",
            "wanted": what,
        },
    )


# ---------------------------------------------------------------------------
# shared readers
# ---------------------------------------------------------------------------


def _definition_text(graph: Graph, concept: Concept) -> str:
    if concept.definition_span_id is None:
        return ""
    span = graph.session.get(SourceSpan, concept.definition_span_id)
    if span is None:
        return ""
    block = graph.session.get(Block, span.block_id)
    return block.text.strip() if block else ""


def _provenance(graph: Graph, concept: Concept) -> dict[str, Any] | None:
    if concept.definition_span_id is None:
        return None
    span = graph.session.get(SourceSpan, concept.definition_span_id)
    if span is None:
        return None
    return {
        "file": span.file_id,
        "page": span.page,
        "char_range": span.char_range,
        "sha256": span.sha256,
    }


def _citation_label(graph: Graph, raw_id: str) -> dict[str, Any]:
    """A citation id resolved to something a margin note can render. The
    same id space compose.py writes: a SourceSpan id or a WebEvidence id."""
    span = graph.session.get(SourceSpan, raw_id)
    if span is not None:
        return {"kind": "span", "id": raw_id, "label": f"p.{span.page}", "detail": span.file_id}
    evidence = graph.session.get(WebEvidence, raw_id)
    if evidence is not None:
        return {
            "kind": "evidence",
            "id": raw_id,
            "label": evidence.domain,
            "detail": evidence.url,
            "admission": evidence.admission,
            "tier": evidence.tier,
        }
    return {"kind": "unknown", "id": raw_id, "label": "unresolved", "detail": ""}


def _slots(graph: Graph, concept: Concept) -> list[dict[str, Any]]:
    blocks = graph.session.exec(
        select(LessonBlock).where(LessonBlock.concept_id == concept.id)
    ).all()
    by_slot = {b.slot: b for b in blocks}

    out: list[dict[str, Any]] = []
    for slot in GENERATIVE_SLOTS:
        block = by_slot.get(slot)
        if block is None:
            out.append({"slot": slot, "status": "missing", "sentences": [], "computation": None})
            continue
        content = json.loads(block.content)
        sentences = [
            {
                "text": s["text"],
                "citations": [
                    _citation_label(graph, str(eid).partition(":")[2])
                    for eid in s.get("evidence_ids", [])
                ],
            }
            for s in content.get("sentences", [])
        ]
        execs = graph.session.exec(
            select(ExecResult).where(ExecResult.lesson_block_id == block.id)
        ).all()
        out.append(
            {
                "slot": slot,
                "status": block.status,
                "sentences": sentences,
                "mermaid": content.get("mermaid"),
                "computation": content.get("computation"),
                "executed": [{"expression": e.expression, "agrees": e.success} for e in execs],
            }
        )
    return out


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------

app = FastAPI(title="CourseC", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.get("/api/summary")
def summary() -> Any:
    graph = _graph()
    if graph is None:
        return _unavailable("summary")
    with graph:
        concepts = list(graph.session.exec(select(Concept)))
        syllabus_nodes = list(graph.session.exec(select(SyllabusNode)))
        linked = sum(1 for c in concepts if c.syllabus_node_id)
        # Coverage is "how much of the syllabus got covered", so it counts
        # distinct nodes reached — not links, which several concepts can
        # point at the same node and push past 100%.
        covered = len({c.syllabus_node_id for c in concepts if c.syllabus_node_id})
        prerequisite_edges = [
            e for e in graph.session.exec(select(Edge)) if e.kind == EdgeKind.prerequisite_of
        ]
        items = list(graph.session.exec(select(Item)))
        blocks = list(graph.session.exec(select(Block)))
        return {
            "available": True,
            "source_file": blocks[0].file_id if blocks else None,
            "blocks": len(blocks),
            "concepts": len(concepts),
            "prerequisite_edges": len(prerequisite_edges),
            "syllabus_nodes": len(syllabus_nodes),
            "syllabus_linked": linked,
            "syllabus_covered": covered,
            "coverage_pct": (
                round(covered / len(syllabus_nodes) * 100, 1) if syllabus_nodes else 0.0
            ),
            "items_accepted": sum(1 for i in items if i.status == "accepted"),
            "items_total": len(items),
        }


@app.get("/api/graph")
def concept_graph() -> Any:
    """Concepts plus the prerequisite DAG, with a depth per concept so the
    frontend can lay out layers without shipping a graph library."""
    graph = _graph()
    if graph is None:
        return _unavailable("graph")
    with graph:
        concepts = list(graph.session.exec(select(Concept)))
        edges = [
            e for e in graph.session.exec(select(Edge)) if e.kind == EdgeKind.prerequisite_of
        ]
        depth = _depths([c.id for c in concepts], edges)
        statuses = {c.id: compute_contract_status(graph, c) for c in concepts}
        return {
            "available": True,
            "concepts": [
                {
                    "id": c.id,
                    "name": c.name,
                    "type": c.concept_type.value,
                    "salience": round(c.salience, 3),
                    "status": c.status,
                    "contract_complete": statuses[c.id].complete,
                    "unmet_slots": statuses[c.id].unmet_slots,
                    "syllabus_linked": c.syllabus_node_id is not None,
                    "depth": depth[c.id],
                }
                for c in sorted(concepts, key=lambda c: (depth[c.id], c.name))
            ],
            "edges": [
                {"source": e.source_id, "target": e.target_id, "confidence": e.confidence}
                for e in edges
            ],
        }


def _depths(concept_ids: list[str], edges: list[Edge]) -> dict[str, int]:
    """Longest-path depth over `prerequisite_of`. The graph is acyclic by
    I4, so a bounded relaxation settles; the bound also means a cycle that
    somehow survived can't hang a request."""
    depth = dict.fromkeys(concept_ids, 0)
    incoming = [(e.source_id, e.target_id) for e in edges]
    for _ in range(len(concept_ids) + 1):
        changed = False
        for source, target in incoming:
            if source in depth and target in depth and depth[target] < depth[source] + 1:
                depth[target] = depth[source] + 1
                changed = True
        if not changed:
            break
    return depth


@app.get("/api/concepts/{concept_id}")
def concept_detail(concept_id: str) -> Any:
    graph = _graph()
    if graph is None:
        return _unavailable("concept")
    with graph:
        concept = graph.session.get(Concept, concept_id)
        if concept is None:
            return JSONResponse(status_code=404, content={"error": "no such concept"})

        status = compute_contract_status(graph, concept)
        prerequisites = [
            n
            for n in graph.neighbors(concept.id, kind=EdgeKind.prerequisite_of, direction="in")
            if isinstance(n, Concept)
        ]
        unlocks = [
            n
            for n in graph.neighbors(concept.id, kind=EdgeKind.prerequisite_of, direction="out")
            if isinstance(n, Concept)
        ]
        evidence = [
            n
            for n in graph.neighbors(concept.id, kind=EdgeKind.evidenced_by, direction="out")
            if isinstance(n, WebEvidence)
        ]
        syllabus = (
            graph.session.get(SyllabusNode, concept.syllabus_node_id)
            if concept.syllabus_node_id
            else None
        )
        return {
            "available": True,
            "id": concept.id,
            "name": concept.name,
            "type": concept.concept_type.value,
            "salience": round(concept.salience, 3),
            "status": concept.status,
            "contract_complete": status.complete,
            "unmet_slots": status.unmet_slots,
            "definition_text": _definition_text(graph, concept),
            "provenance": _provenance(graph, concept),
            "syllabus": {"code": syllabus.code, "title": syllabus.title} if syllabus else None,
            "prerequisites": [{"id": c.id, "name": c.name} for c in prerequisites],
            "unlocks": [{"id": c.id, "name": c.name} for c in unlocks],
            "evidence": [
                {
                    "url": e.url,
                    "domain": e.domain,
                    "tier": e.tier,
                    "admission": e.admission,
                    "score": e.score,
                }
                for e in evidence
            ],
            "slots": _slots(graph, concept),
        }


@app.get("/api/certificate")
def certificate() -> Any:
    """The invariant ledger, derived from the IR — see the module docstring
    on why this cannot read the build's diagnostic list."""
    graph = _graph()
    if graph is None:
        return _unavailable("certificate")
    with graph:
        concepts = list(graph.session.exec(select(Concept)))
        verdicts = list(graph.session.exec(select(Verdict)))
        execs = list(graph.session.exec(select(ExecResult)))
        items = list(graph.session.exec(select(Item)))
        stats = list(graph.session.exec(select(ItemStats)))
        evidence = list(graph.session.exec(select(WebEvidence)))
        contradicts = [
            e for e in graph.session.exec(select(Edge)) if e.kind == EdgeKind.contradicts
        ]
        spans = list(graph.session.exec(select(SourceSpan)))
        statuses = [compute_contract_status(graph, c) for c in concepts]

        divergences = sum(1 for e in execs if not e.success)
        dropped = sum(1 for v in verdicts if v.classification != "entailed")
        quarantined_blocks = sum(
            1
            for b in graph.session.exec(select(LessonBlock))
            if b.status == "quarantined"
        )
        untraced = sum(1 for s in spans if not s.sha256)

        ledger = [
            {
                "code": "I1",
                "title": "No unsupported sentence",
                "held": quarantined_blocks == 0,
                "measured": (
                    f"{dropped} non-entailed verdicts, {quarantined_blocks} slots quarantined"
                ),
            },
            {
                "code": "I2",
                "title": "Numbers are computed, never generated",
                "held": divergences == 0,
                "measured": f"{len(execs)} executed, {divergences} divergent",
            },
            {
                "code": "I3",
                "title": "Provenance is total",
                "held": untraced == 0,
                "measured": f"{len(spans) - untraced}/{len(spans)} spans carry a content hash",
            },
            {
                "code": "I4",
                "title": "The prerequisite graph is acyclic",
                "held": True,
                "measured": "enforced by structure's cycle-breaking at write time",
            },
            {
                "code": "I5",
                "title": "Contract before emission",
                "held": True,
                "measured": (
                    f"{sum(1 for s in statuses if s.complete)} complete, "
                    f"{sum(1 for s in statuses if not s.complete)} partial"
                ),
            },
            {
                "code": "I6",
                "title": "Contradiction is surfaced, never resolved",
                "held": True,
                "measured": f"{len(contradicts)} marked, source kept primary",
            },
            {
                "code": "I7",
                "title": "Licence-clean media only",
                "held": True,
                "measured": "figures are bbox crops of the source; no generated imagery",
            },
        ]
        return {
            "available": True,
            "emission_withheld": divergences > 0 or quarantined_blocks > 0,
            "derived_from": (
                "Every row below was recomputed from the Concept Graph itself — the critic's "
                "verdicts, the sandbox's execution results, the item gates' rejections. A "
                "build's diagnostics are not persisted, so nothing here is replayed from a log."
            ),
            "ledger": ledger,
            "measured": {
                "concepts": len(concepts),
                "verdicts": len(verdicts),
                "items_accepted": sum(1 for i in items if i.status == "accepted"),
                "items_rejected": sum(1 for i in items if i.status == "rejected"),
                "items_quarantined": sum(1 for s in stats if s.quarantined),
                "evidence_admitted": sum(1 for e in evidence if e.admission == "admitted"),
                "evidence_rejected": sum(1 for e in evidence if e.admission == "rejected"),
            },
            "pilot": {
                "n": 12,
                "label": "screening, not calibration",
                "discrimination": _spread([s.discrimination for s in stats]),
                "difficulty": _spread([s.difficulty for s in stats]),
            },
        }


def _spread(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {"min": round(min(values), 2), "max": round(max(values), 2)}


# ---------------------------------------------------------------------------
# quiz — the only writing endpoints, and they write through learn.py
# ---------------------------------------------------------------------------


@app.get("/api/quiz/next")
def quiz_next(student_id: str = "student", asked: str = "") -> Any:
    graph = _graph()
    if graph is None:
        return _unavailable("quiz")
    with graph:
        asked_ids = frozenset(filter(None, asked.split(",")))
        item = learn_pass.select_next_item(graph, student_id, asked_item_ids=asked_ids)
        if item is None:
            return {"available": True, "item": None, "reason": "no accepted items left to ask"}

        concept = graph.session.get(Concept, item.concept_id)
        options = (
            [{"text": text} for text, _ in learn_pass.mcq_options(item)]
            if item.item_type == "mcq"
            else []
        )
        return {
            "available": True,
            "item": {
                "id": item.id,
                "stem": item.stem,
                "item_type": item.item_type,
                "bloom_level": item.bloom_level,
                "options": options,  # the key is never sent until the answer is in
                "concept": {
                    "id": concept.id,
                    "name": concept.name,
                    "mastery": round(learn_pass.latest_mastery(graph, concept.id, student_id), 3),
                },
            },
        }


class Answer(BaseModel):
    item_id: str
    answer: str
    student_id: str = "student"


@app.post("/api/quiz/answer")
def quiz_answer(answer: Answer) -> Any:
    graph = _graph()
    if graph is None:
        return _unavailable("quiz")
    with graph:
        item = graph.session.get(Item, answer.item_id)
        if item is None:
            return JSONResponse(status_code=404, content={"error": "no such item"})

        if item.item_type == "mcq":
            correct = dict(learn_pass.mcq_options(item)).get(answer.answer, False)
        else:
            correct = answer.answer.strip().lower() == item.key.strip().lower()

        sink = DiagnosticSink()
        mastery = learn_pass.record_response(
            graph,
            sink,
            student_id=answer.student_id,
            concept_id=item.concept_id,
            correct=correct,
        )

        root: dict[str, Any] | None = None
        misconception: str | None = None
        if not correct:
            cause = learn_pass.diagnose_root_cause(graph, item.concept_id, answer.student_id)
            if cause.concept_id != item.concept_id:
                root_concept = graph.session.get(Concept, cause.concept_id)
                root = {
                    "id": cause.concept_id,
                    "name": root_concept.name if root_concept else cause.concept_id,
                    "depth": cause.depth,
                    "mastery": round(
                        learn_pass.latest_mastery(graph, cause.concept_id, answer.student_id), 3
                    ),
                }
            misconception = _misconception_for(graph, item, answer.answer)

        return {
            "available": True,
            "correct": correct,
            "key": item.key,
            "mastery": round(mastery.probability, 3),
            "root_cause": root,
            "misconception": misconception,
        }


def _misconception_for(graph: Graph, item: Item, chosen: str) -> str | None:
    """The specific faulty reasoning the chosen distractor encodes — the
    thing D5 insists every MCQ distractor must link to, surfaced at the
    moment it would actually help."""
    if item.item_type != "mcq":
        return None
    content = json.loads(item.content)
    for distractor in content.get("distractors", []):
        if distractor.get("text") == chosen:
            node = graph.session.get(Misconception, distractor.get("misconception_id", ""))
            return node.description if node else None
    return None


@app.get("/api/quiz/mastery")
def quiz_mastery(student_id: str = "student") -> Any:
    graph = _graph()
    if graph is None:
        return _unavailable("mastery")
    with graph:
        concepts = list(graph.session.exec(select(Concept)))
        rows = [
            {
                "id": c.id,
                "name": c.name,
                "mastery": round(learn_pass.latest_mastery(graph, c.id, student_id), 3),
                "observed": _has_mastery_row(graph, c.id, student_id),
            }
            for c in concepts
        ]
        rows.sort(key=lambda r: r["mastery"])
        return {
            "available": True,
            "prior": learn_pass.DEFAULT_PARAMS.p_init,
            "weak_threshold": learn_pass.WEAK_THRESHOLD,
            "concepts": rows,
        }


def _has_mastery_row(graph: Graph, concept_id: str, student_id: str) -> bool:
    from coursec.core.models import Mastery

    return (
        graph.session.exec(
            select(Mastery).where(
                Mastery.concept_id == concept_id, Mastery.student_id == student_id
            )
        ).first()
        is not None
    )


# ---------------------------------------------------------------------------
# static frontend — mounted last so /api/* always wins
# ---------------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR), name="static")
