"""The dossier — part of the compose pass (CLAUDE.md §4: compose reads
"dossiers"). Assembles everything a generation call is allowed to ground on
for one Concept: its source span, admitted/weak evidence, one-line
prerequisite summaries, cohort level, and its unmet contract slots.

Every evidence-bearing line carries a bracketed id — `[SPAN:<id>]` or
`[EVID:<id>]` — so compose.py's generator can cite exactly what it used, and
verify.py's critic can look a citation back up without ever seeing this
module's output (CLAUDE.md D4: "[the critic] sees ... only its cited spans,
not the dossier").

The prefix is built by iterating already-sorted, already-persisted rows in a
fixed order, so two calls against the same graph state produce a
byte-identical string — see test_dossier.py's stability test. Truncation
under the token budget drops the *worst*-scoring evidence first, never
randomly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import select

from coursec.core.graph import Graph
from coursec.core.models import Block, Concept, Edge, EdgeKind, SourceSpan, WebEvidence

PASS_NAME = "compose"

TOKEN_BUDGET_WORDS = 800  # demo-grade proxy for a token budget


def _definition_text(graph: Graph, concept: Concept) -> str:
    if concept.definition_span_id is None:
        return ""
    span = graph.session.get(SourceSpan, concept.definition_span_id)
    if span is None:
        return ""
    block = graph.session.get(Block, span.block_id)
    return block.text.strip() if block else ""


def _admitted_evidence(graph: Graph, concept: Concept) -> list[WebEvidence]:
    edges = graph.session.exec(
        select(Edge).where(Edge.source_id == concept.id, Edge.kind == EdgeKind.evidenced_by)
    ).all()
    rows = []
    for edge in edges:
        row = graph.session.get(WebEvidence, edge.target_id)
        if row is not None and row.admission in ("admitted", "weak"):
            rows.append(row)
    # Best-first, deterministic tie-break on id so ties never depend on
    # insertion order across runs.
    rows.sort(key=lambda r: (-(r.score or 0.0), r.id))
    return rows


def _direct_prerequisites(graph: Graph, concept: Concept) -> list[Concept]:
    neighbors = graph.neighbors(concept.id, kind=EdgeKind.prerequisite_of, direction="in")
    prereqs = [n for n in neighbors if isinstance(n, Concept)]
    return sorted(prereqs, key=lambda c: c.name)


def _existing_slots(graph: Graph, concept: Concept) -> set[str]:
    from coursec.core.models import LessonBlock

    rows = graph.session.exec(
        select(LessonBlock).where(
            LessonBlock.concept_id == concept.id, LessonBlock.status != "quarantined"
        )
    ).all()
    return {row.slot for row in rows}


@dataclass
class Dossier:
    concept_id: str
    prefix: str
    source_span_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    unmet_slots: list[str] = field(default_factory=list)

    @property
    def has_grounding(self) -> bool:
        return bool(self.source_span_ids or self.evidence_ids)


def build_dossier(
    graph: Graph,
    concept: Concept,
    *,
    all_slots: tuple[str, ...],
    cohort_level: str = "intro",
    token_budget: int = TOKEN_BUDGET_WORDS,
) -> Dossier:
    lines: list[str] = [
        f"CONCEPT: {concept.name} ({concept.concept_type.value})",
        f"COHORT: {cohort_level}",
    ]
    word_count = sum(len(line.split()) for line in lines)

    source_span_ids: list[str] = []
    if concept.definition_span_id is not None:
        text = _definition_text(graph, concept)
        if text:
            line = f"[SPAN:{concept.definition_span_id}] {text}"
            lines.append(line)
            word_count += len(line.split())
            source_span_ids.append(concept.definition_span_id)

    prereq_lines = []
    for prereq in _direct_prerequisites(graph, concept):
        summary = _definition_text(graph, prereq).split(".")[0].strip()
        prereq_lines.append(f"PREREQUISITE {prereq.name}: {summary}.")
    if prereq_lines:
        lines.append("PREREQUISITES:")
        for line in prereq_lines:
            lines.append(line)
            word_count += len(line.split())

    evidence_ids: list[str] = []
    admitted = _admitted_evidence(graph, concept)
    if admitted:
        lines.append("EVIDENCE:")
    for row in admitted:
        line = f"[EVID:{row.id}] ({row.tier}, {row.admission}) {row.chunk_text}"
        cost = len(line.split())
        if word_count + cost > token_budget:
            continue  # ascending-score drop: rows are best-first, so every
            # remaining (worse) row is skipped too, but we keep scanning in
            # case a later, shorter row still fits.
        lines.append(line)
        word_count += cost
        evidence_ids.append(row.id)

    unmet_slots = [s for s in all_slots if s not in _existing_slots(graph, concept)]
    if unmet_slots:
        lines.append(f"UNMET SLOTS: {', '.join(unmet_slots)}")

    return Dossier(
        concept_id=concept.id,
        prefix="\n".join(lines),
        source_span_ids=source_span_ids,
        evidence_ids=evidence_ids,
        unmet_slots=unmet_slots,
    )
