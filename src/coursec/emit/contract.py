"""Contract enforcement — the 5 MVP slots from CLAUDE.md I5: definition,
intuition, worked example, visual-or-analogy, and >=2 assessment items.

A Concept is emitted only with a status attached; `partial` concepts list
their unmet slots rather than being silently dropped or silently emitted
as if complete.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import select

from coursec.core.graph import Graph
from coursec.core.models import Concept, Item, LessonBlock

GENERATIVE_SLOTS: tuple[str, ...] = (
    "definition",
    "intuition",
    "worked_example",
    "visual_or_analogy",
)
MIN_ITEMS = 2


@dataclass
class ContractStatus:
    concept_id: str
    complete: bool
    unmet_slots: list[str] = field(default_factory=list)


def compute_contract_status(graph: Graph, concept: Concept) -> ContractStatus:
    blocks = graph.session.exec(
        select(LessonBlock).where(
            LessonBlock.concept_id == concept.id, LessonBlock.status != "quarantined"
        )
    ).all()
    present_slots = {b.slot for b in blocks}
    unmet = [slot for slot in GENERATIVE_SLOTS if slot not in present_slots]

    accepted_items = graph.session.exec(
        select(Item).where(Item.concept_id == concept.id, Item.status != "quarantined")
    ).all()
    if len(accepted_items) < MIN_ITEMS:
        unmet.append(f"items (have {len(accepted_items)}, need {MIN_ITEMS})")

    return ContractStatus(concept_id=concept.id, complete=not unmet, unmet_slots=unmet)


def compute_all_contract_statuses(
    graph: Graph, concepts: list[Concept]
) -> dict[str, ContractStatus]:
    return {c.id: compute_contract_status(graph, c) for c in concepts}
