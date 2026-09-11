import json

import pytest

from coursec.core.graph import Graph, MissingNodeError, OwnershipViolation
from coursec.core.models import Block, BlockType, Concept, ConceptType, Edge, EdgeKind


def _concept(name: str) -> Concept:
    return Concept(
        created_by_pass="understand",
        content_hash="h",
        name=name,
        concept_type=ConceptType.definition,
    )


def test_add_rejects_ownership_violation() -> None:
    graph = Graph()
    block = Block(
        created_by_pass="understand",  # wrong — Block is owned by "ingest"
        content_hash="h",
        file_id="f",
        page=0,
        bbox=[0, 0, 1, 1],
        text="x",
        block_type=BlockType.paragraph,
    )
    with pytest.raises(OwnershipViolation):
        graph.add(block)


def test_add_accepts_correct_owner() -> None:
    graph = Graph()
    block = Block(
        created_by_pass="ingest",
        content_hash="h",
        file_id="f",
        page=0,
        bbox=[0, 0, 1, 1],
        text="x",
        block_type=BlockType.paragraph,
    )
    added = graph.add(block)
    assert graph.get(added.id) is not None


def test_edge_referencing_missing_node_raises() -> None:
    graph = Graph()
    concept = graph.add(_concept("real"))
    edge = Edge(
        created_by_pass="structure",
        content_hash="h",
        source_id=concept.id,
        target_id="does-not-exist",
        kind=EdgeKind.part_of,
    )
    with pytest.raises(MissingNodeError):
        graph.add(edge)


def test_edge_ownership_violation_raises() -> None:
    graph = Graph()
    a = graph.add(_concept("a"))
    b = graph.add(_concept("b"))
    edge = Edge(
        created_by_pass="evidence",  # wrong — part_of is owned by "structure"
        content_hash="h",
        source_id=a.id,
        target_id=b.id,
        kind=EdgeKind.part_of,
    )
    with pytest.raises(OwnershipViolation):
        graph.add(edge)


def test_created_by_pass_is_immutable_after_write() -> None:
    graph = Graph()
    concept = graph.add(_concept("immutable-test"))
    with pytest.raises(ValueError, match="immutable"):
        concept.created_by_pass = "structure"


def test_created_by_pass_can_be_set_once_at_construction() -> None:
    # The guard must not fire on the initial assignment during __init__.
    concept = _concept("fresh")
    assert concept.created_by_pass == "understand"


def test_to_json_from_json_round_trip_identity() -> None:
    graph = Graph()
    a = graph.add(_concept("Ohm's law"))
    b = graph.add(_concept("V = IR relationship"))
    graph.add(
        Edge(
            created_by_pass="structure",
            content_hash="h",
            source_id=a.id,
            target_id=b.id,
            kind=EdgeKind.part_of,
        )
    )

    dump = graph.to_json()

    fresh = Graph()  # a completely separate database
    fresh.from_json(dump)

    assert json.loads(fresh.to_json()) == json.loads(dump)


def test_neighbors_follows_edge_direction() -> None:
    graph = Graph()
    a = graph.add(_concept("a"))
    b = graph.add(_concept("b"))
    graph.add(
        Edge(
            created_by_pass="structure",
            content_hash="h",
            source_id=a.id,
            target_id=b.id,
            kind=EdgeKind.prerequisite_of,
        )
    )
    assert [n.id for n in graph.neighbors(a.id, direction="out")] == [b.id]
    assert [n.id for n in graph.neighbors(b.id, direction="out")] == []
    assert [n.id for n in graph.neighbors(b.id, direction="in")] == [a.id]
    assert {n.id for n in graph.neighbors(a.id, direction="both")} == {b.id}
    assert [n.id for n in graph.neighbors(a.id, kind=EdgeKind.part_of, direction="out")] == []
    assert [n.id for n in graph.neighbors(b.id, kind=EdgeKind.part_of, direction="in")] == []


def test_neighbors_rejects_invalid_direction() -> None:
    graph = Graph()
    a = graph.add(_concept("a"))
    with pytest.raises(ValueError, match="direction"):
        graph.neighbors(a.id, direction="sideways")


def test_add_rejects_node_type_with_no_declared_owner(monkeypatch) -> None:
    import coursec.core.graph as graph_module

    graph = Graph()
    monkeypatch.delitem(graph_module.NODE_TYPE_OWNER, Concept)
    with pytest.raises(OwnershipViolation, match="no declared owner"):
        graph.add(_concept("orphaned type"))


def test_add_rejects_edge_kind_with_no_declared_owner(monkeypatch) -> None:
    import coursec.core.graph as graph_module

    graph = Graph()
    a = graph.add(_concept("a"))
    b = graph.add(_concept("b"))
    monkeypatch.delitem(graph_module.EDGE_KIND_OWNER, EdgeKind.mastery_of)
    edge = Edge(
        created_by_pass="learn",
        content_hash="h",
        source_id=a.id,
        target_id=b.id,
        kind=EdgeKind.mastery_of,
    )
    with pytest.raises(OwnershipViolation, match="no declared owner"):
        graph.add(edge)


def test_ancestors_and_descendants_walk_transitively() -> None:
    graph = Graph()
    nodes = {name: graph.add(_concept(name)) for name in "abc"}
    # a -prerequisite_of-> b -prerequisite_of-> c
    graph.add(
        Edge(
            created_by_pass="structure",
            content_hash="h",
            source_id=nodes["a"].id,
            target_id=nodes["b"].id,
            kind=EdgeKind.prerequisite_of,
        )
    )
    graph.add(
        Edge(
            created_by_pass="structure",
            content_hash="h",
            source_id=nodes["b"].id,
            target_id=nodes["c"].id,
            kind=EdgeKind.prerequisite_of,
        )
    )

    ancestor_ids = {n.id for n in graph.ancestors(nodes["c"].id, EdgeKind.prerequisite_of)}
    assert ancestor_ids == {nodes["a"].id, nodes["b"].id}

    descendant_ids = {n.id for n in graph.descendants(nodes["a"].id, EdgeKind.prerequisite_of)}
    assert descendant_ids == {nodes["b"].id, nodes["c"].id}


def test_subgraph_restricts_to_given_nodes() -> None:
    graph = Graph()
    a = graph.add(_concept("a"))
    b = graph.add(_concept("b"))
    c = graph.add(_concept("c"))
    graph.add(
        Edge(
            created_by_pass="structure",
            content_hash="h",
            source_id=a.id,
            target_id=b.id,
            kind=EdgeKind.part_of,
        )
    )
    graph.add(
        Edge(
            created_by_pass="structure",
            content_hash="h",
            source_id=b.id,
            target_id=c.id,
            kind=EdgeKind.part_of,
        )
    )

    sub = graph.subgraph([a.id, b.id])
    concept_ids = {row["id"] for row in sub["nodes"]["Concept"]}
    assert concept_ids == {a.id, b.id}
    assert len(sub["edges"]) == 1  # only a->b; b->c falls outside the set
