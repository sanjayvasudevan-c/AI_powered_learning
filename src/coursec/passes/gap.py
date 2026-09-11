"""The gap pass — CLAUDE.md §4: owns `GapVector`, `RetrievalBudget`; reads
`Concept`, `SyllabusNode`.

Per Concept, a four-field `GapVector` (PROMPTS.md D3):
  coverage     — unlinked syllabus node
  depth        — below a token floor, or no formal statement
  modality     — no worked example, no visual nearby
  prerequisite — an ancestor concept is itself uncovered

`RetrievalBudget` is allocated proportional to the vector under a global
cap — scale-invariant by construction: doubling the concept count competes
the same cap across twice as many claims, so no single concept's share can
double (see test_gap.py's scale-invariance test).
"""

from __future__ import annotations

from dataclasses import dataclass

from coursec.core.graph import Graph
from coursec.core.models import Block, BlockType, Concept, ConceptType, EdgeKind, SourceSpan

PASS_NAME = "gap"

DEPTH_TOKEN_FLOOR = 40  # a definition shorter than this (in words) is "shallow"


def _definition_text(graph: Graph, concept: Concept) -> str:
    if concept.definition_span_id is None:
        return ""
    span = graph.session.get(SourceSpan, concept.definition_span_id)
    if span is None:
        return ""
    block = graph.session.get(Block, span.block_id)
    return block.text.strip() if block else ""


def _section_heading_id(graph: Graph, concept: Concept) -> str | None:
    headings = graph.neighbors(concept.id, kind=EdgeKind.part_of, direction="out")
    return headings[0].id if headings else None


def _section_has_visual(graph: Graph, heading_id: str | None, definition_page: int) -> bool:
    """Demo-grade proxy for "this section has a visual": a bound-caption
    figure on the same page as the concept's own definition. A full
    section-to-Block index isn't built at this stage — `part_of` only
    connects Concepts to their heading, not every Block in a section."""
    if heading_id is None:
        return False
    from sqlmodel import select

    figures = graph.session.exec(
        select(Block).where(Block.page == definition_page, Block.block_type == BlockType.figure)
    ).all()
    return any(f.bound_caption_id is not None for f in figures)


def _section_has_worked_example(
    graph: Graph, heading_id: str | None, all_concepts: list[Concept]
) -> bool:
    if heading_id is None:
        return False
    siblings = graph.neighbors(heading_id, kind=EdgeKind.part_of, direction="in")
    return any(s.concept_type == ConceptType.example for s in siblings)


@dataclass
class GapVector:
    coverage: float
    depth: float
    modality: float
    prerequisite: float

    @property
    def total(self) -> float:
        return self.coverage + self.depth + self.modality + self.prerequisite


def compute_gap_vector(
    graph: Graph,
    concept: Concept,
    all_concepts: list[Concept],
    *,
    uncovered_concept_ids: set[str],
) -> GapVector:
    coverage = 1.0 if concept.syllabus_node_id is None else 0.0

    definition_text = _definition_text(graph, concept)
    depth = 1.0 if not definition_text or len(definition_text.split()) < DEPTH_TOKEN_FLOOR else 0.0

    heading_id = _section_heading_id(graph, concept)
    definition_page = 0
    if concept.definition_span_id is not None:
        span = graph.session.get(SourceSpan, concept.definition_span_id)
        if span is not None:
            definition_page = span.page
    has_visual = _section_has_visual(graph, heading_id, definition_page)
    has_example = _section_has_worked_example(graph, heading_id, all_concepts)
    modality = 0.0 if (has_visual and has_example) else 1.0

    ancestors = graph.ancestors(concept.id, EdgeKind.prerequisite_of)
    prerequisite = (
        1.0 if any(a.id in uncovered_concept_ids for a in ancestors) else 0.0
    )

    return GapVector(coverage=coverage, depth=depth, modality=modality, prerequisite=prerequisite)


def compute_gap_vectors(
    graph: Graph, concepts: list[Concept]
) -> dict[str, GapVector]:
    uncovered_concept_ids = {c.id for c in concepts if c.syllabus_node_id is None}
    return {
        c.id: compute_gap_vector(graph, c, concepts, uncovered_concept_ids=uncovered_concept_ids)
        for c in concepts
    }


@dataclass
class RetrievalBudget:
    concept_id: str
    queries: int


def allocate_budgets(
    gap_vectors: dict[str, GapVector], *, global_cap: int
) -> dict[str, RetrievalBudget]:
    """Proportional allocation under a global cap. A fully-covered set of
    concepts (every GapVector all-zero) allocates a total budget of zero —
    the system must be able to decide not to search, not just search less."""
    total_gap = sum(v.total for v in gap_vectors.values())
    if total_gap <= 0:
        return {cid: RetrievalBudget(cid, 0) for cid in gap_vectors}

    budgets: dict[str, RetrievalBudget] = {}
    allocated = 0
    ids = list(gap_vectors)
    for i, concept_id in enumerate(ids):
        share = gap_vectors[concept_id].total / total_gap
        if i == len(ids) - 1:
            # last one takes the remainder so the total never drifts above
            # global_cap from independent rounding.
            queries = max(0, global_cap - allocated)
        else:
            queries = round(share * global_cap)
        allocated += queries
        budgets[concept_id] = RetrievalBudget(concept_id, queries)
    return budgets


def gap_histogram(gap_vectors: dict[str, GapVector]) -> dict[str, int]:
    """Count of concepts with each dimension set, plus how many have no gap
    at all — the printable summary `coursec build` reports."""
    dimensions = ("coverage", "depth", "modality", "prerequisite")
    counts = {dim: 0 for dim in dimensions}
    no_gap = 0
    for vector in gap_vectors.values():
        for dim in dimensions:
            if getattr(vector, dim):
                counts[dim] += 1
        if vector.total == 0:
            no_gap += 1
    counts["no_gap"] = no_gap
    return counts
