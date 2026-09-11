from conftest import add_block_with_span

from coursec.core.graph import Graph
from coursec.core.models import (
    Block,
    BlockType,
    Concept,
    ConceptType,
    Edge,
    EdgeKind,
    SyllabusNode,
)
from coursec.passes import gap
from coursec.passes.understand import _span_id_for_block

LONG_DEFINITION = " ".join(["word"] * 60)  # well over DEPTH_TOKEN_FLOOR


def _syllabus_node(graph: Graph) -> SyllabusNode:
    return graph.add(
        SyllabusNode(
            created_by_pass="understand", content_hash="h", code="1.1", title="t", order=1
        )
    )


def _concept(graph: Graph, name: str, definition: str, *, syllabus_node_id=None) -> Concept:
    block = add_block_with_span(graph, definition)
    return graph.add(
        Concept(
            created_by_pass="understand",
            content_hash="h",
            name=name,
            concept_type=ConceptType.definition,
            definition_span_id=_span_id_for_block(graph, block.id),
            syllabus_node_id=syllabus_node_id,
        )
    )


def test_coverage_gap_is_set_when_unlinked(graph: Graph) -> None:
    concept = _concept(graph, "a", LONG_DEFINITION)
    vector = gap.compute_gap_vector(graph, concept, [concept], uncovered_concept_ids=set())
    assert vector.coverage == 1.0


def test_coverage_gap_is_clear_when_linked(graph: Graph) -> None:
    node = _syllabus_node(graph)
    concept = _concept(graph, "a", LONG_DEFINITION, syllabus_node_id=node.id)
    vector = gap.compute_gap_vector(graph, concept, [concept], uncovered_concept_ids=set())
    assert vector.coverage == 0.0


def test_depth_gap_set_below_token_floor(graph: Graph) -> None:
    concept = _concept(graph, "a", "short definition")
    vector = gap.compute_gap_vector(graph, concept, [concept], uncovered_concept_ids=set())
    assert vector.depth == 1.0


def test_depth_gap_clear_above_token_floor(graph: Graph) -> None:
    concept = _concept(graph, "a", LONG_DEFINITION)
    vector = gap.compute_gap_vector(graph, concept, [concept], uncovered_concept_ids=set())
    assert vector.depth == 0.0


def test_prerequisite_gap_set_when_ancestor_uncovered(graph: Graph) -> None:
    prereq = _concept(graph, "prereq", LONG_DEFINITION)  # uncovered: no syllabus link
    dependent = _concept(graph, "dependent", LONG_DEFINITION)
    graph.add(
        Edge(
            created_by_pass="structure",
            content_hash="h",
            source_id=prereq.id,
            target_id=dependent.id,
            kind=EdgeKind.prerequisite_of,
        )
    )
    vector = gap.compute_gap_vector(
        graph, dependent, [prereq, dependent], uncovered_concept_ids={prereq.id}
    )
    assert vector.prerequisite == 1.0


def test_prerequisite_gap_clear_when_no_uncovered_ancestor(graph: Graph) -> None:
    node = _syllabus_node(graph)
    prereq = _concept(graph, "prereq", LONG_DEFINITION, syllabus_node_id=node.id)
    dependent = _concept(graph, "dependent", LONG_DEFINITION)
    graph.add(
        Edge(
            created_by_pass="structure",
            content_hash="h",
            source_id=prereq.id,
            target_id=dependent.id,
            kind=EdgeKind.prerequisite_of,
        )
    )
    vector = gap.compute_gap_vector(
        graph, dependent, [prereq, dependent], uncovered_concept_ids=set()
    )
    assert vector.prerequisite == 0.0


def _heading(graph: Graph, text: str) -> Block:
    return graph.add(
        Block(
            created_by_pass="ingest",
            content_hash="h",
            file_id="t.pdf",
            page=0,
            bbox=[0, 0, 1, 1],
            text=text,
            block_type=BlockType.heading,
        )
    )


def test_fully_covered_fixture_allocates_zero_total_budget(graph: Graph) -> None:
    heading = _heading(graph, "1.1 Section")
    node = _syllabus_node(graph)

    definition_concept = _concept(graph, "a", LONG_DEFINITION, syllabus_node_id=node.id)
    example_concept = _concept(
        graph, "a worked example", LONG_DEFINITION, syllabus_node_id=node.id
    )
    example_concept.concept_type = ConceptType.example
    graph.session.add(example_concept)
    graph.session.commit()
    for c in (definition_concept, example_concept):
        graph.add(
            Edge(
                created_by_pass="structure",
                content_hash=f"po:{c.id}",
                source_id=c.id,
                target_id=heading.id,
                kind=EdgeKind.part_of,
            )
        )
    figure = graph.add(
        Block(
            created_by_pass="ingest",
            content_hash="h2",
            file_id="t.pdf",
            page=0,
            bbox=[0, 0, 1, 1],
            text="",
            block_type=BlockType.figure,
            bound_caption_id="anything-not-none",
        )
    )
    assert figure  # keep the reference; the figure's presence is what matters

    concepts = [definition_concept, example_concept]
    vectors = gap.compute_gap_vectors(graph, concepts)
    for v in vectors.values():
        assert v.total == 0.0

    budgets = gap.allocate_budgets(vectors, global_cap=20)
    assert sum(b.queries for b in budgets.values()) == 0


def test_scale_invariance_doubling_concepts_does_not_double_per_concept_spend(
    graph: Graph,
) -> None:
    def make_gapped_concepts(n: int) -> list[Concept]:
        return [_concept(graph, f"c{i}", "short") for i in range(n)]  # all shallow -> depth gap

    small = make_gapped_concepts(4)
    small_vectors = gap.compute_gap_vectors(graph, small)
    small_budgets = gap.allocate_budgets(small_vectors, global_cap=20)
    small_per_concept = small_budgets[small[0].id].queries

    large = make_gapped_concepts(8)  # 4 more, same shape of gap
    large_vectors = gap.compute_gap_vectors(graph, small + large)
    large_budgets = gap.allocate_budgets(large_vectors, global_cap=20)
    large_per_concept = large_budgets[large[0].id].queries

    assert large_per_concept <= small_per_concept
    assert large_per_concept * 2 <= small_per_concept * 2  # sanity: never doubles
