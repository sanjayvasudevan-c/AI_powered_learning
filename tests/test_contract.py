from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType, Item, LessonBlock
from coursec.emit import contract


def _concept(graph: Graph, name: str = "c") -> Concept:
    return graph.add(
        Concept(
            created_by_pass="understand", content_hash="h", name=name,
            concept_type=ConceptType.definition,
        )
    )


def _lesson_block(graph: Graph, concept: Concept, slot: str, status: str = "ok") -> LessonBlock:
    return graph.add(
        LessonBlock(
            created_by_pass="compose", content_hash=f"h{slot}", concept_id=concept.id,
            slot=slot, content="{}", status=status,
        )
    )


def _item(graph: Graph, concept: Concept, status: str = "accepted") -> Item:
    return graph.add(
        Item(
            created_by_pass="assess", content_hash=f"i{status}{concept.id}",
            concept_id=concept.id, bloom_level="remember", item_type="short",
            stem="s", key="k", status=status,
        )
    )


def test_concept_with_nothing_is_maximally_partial(graph: Graph) -> None:
    concept = _concept(graph)
    status = contract.compute_contract_status(graph, concept)
    assert status.complete is False
    assert set(contract.GENERATIVE_SLOTS) <= set(
        s for s in status.unmet_slots if not s.startswith("items")
    )


def test_concept_with_all_slots_and_enough_items_is_complete(graph: Graph) -> None:
    concept = _concept(graph)
    for slot in contract.GENERATIVE_SLOTS:
        _lesson_block(graph, concept, slot)
    _item(graph, concept)
    _item(graph, concept)
    status = contract.compute_contract_status(graph, concept)
    assert status.complete is True
    assert status.unmet_slots == []


def test_quarantined_slot_counts_as_unmet(graph: Graph) -> None:
    concept = _concept(graph)
    for slot in contract.GENERATIVE_SLOTS:
        _lesson_block(graph, concept, slot, status="quarantined" if slot == "intuition" else "ok")
    _item(graph, concept)
    _item(graph, concept)
    status = contract.compute_contract_status(graph, concept)
    assert status.complete is False
    assert "intuition" in status.unmet_slots


def test_fewer_than_min_items_is_unmet(graph: Graph) -> None:
    concept = _concept(graph)
    for slot in contract.GENERATIVE_SLOTS:
        _lesson_block(graph, concept, slot)
    _item(graph, concept)  # only one, need 2
    status = contract.compute_contract_status(graph, concept)
    assert status.complete is False
    assert any(s.startswith("items") for s in status.unmet_slots)


def test_compute_all_contract_statuses_keys_by_concept_id(graph: Graph) -> None:
    a = _concept(graph, "a")
    b = _concept(graph, "b")
    statuses = contract.compute_all_contract_statuses(graph, [a, b])
    assert set(statuses) == {a.id, b.id}
