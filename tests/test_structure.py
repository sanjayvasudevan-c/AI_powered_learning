import json

from conftest import add_block_with_span
from hypothesis import given
from hypothesis import strategies as st

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Block, BlockType, Concept, ConceptType, Edge, EdgeKind
from coursec.passes import structure
from coursec.passes.structure import EdgeCandidate
from coursec.passes.understand import _span_id_for_block


def _concept(graph: Graph, name: str, definition: str) -> Concept:
    span_block = add_block_with_span(graph, definition)
    return graph.add(
        Concept(
            created_by_pass="understand",
            content_hash="h",
            name=name,
            concept_type=ConceptType.definition,
            definition_span_id=_span_id_for_block(graph, span_block.id),
        )
    )


def test_find_mention_pairs_detects_name_in_definition_text(graph: Graph) -> None:
    prereq = _concept(graph, "voltage", "Voltage is electric potential difference.")
    dependent = _concept(graph, "Ohm's law", "Ohm's law relates voltage, current, and resistance.")

    pairs = structure.find_mention_pairs(graph, [prereq, dependent])

    assert (dependent.id, prereq.id) in pairs  # dependent's text mentions prereq's name
    assert (prereq.id, dependent.id) not in pairs  # not mentioned the other way


def test_find_cooccurrence_pairs_links_same_section() -> None:
    section_by_concept = {"a": "heading-1", "b": "heading-1", "c": "heading-2", "d": None}
    pairs = structure.find_cooccurrence_pairs(section_by_concept)
    assert ("a", "b") in pairs
    assert ("b", "a") in pairs
    assert ("a", "c") not in pairs
    assert not any("d" in pair for pair in pairs)


def test_judge_and_fuse_requires_mention_signal_too(graph: Graph) -> None:
    prereq = _concept(graph, "voltage", "Voltage is electric potential difference.")
    dependent = _concept(graph, "Ohm's law", "Ohm's law relates voltage, current, and resistance.")
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps({"prerequisite": "A", "confidence": 0.9})

    mention_pairs = structure.find_mention_pairs(graph, [prereq, dependent])

    # The LLM says whichever concept comes first alphabetically-by-id is "A".
    # We just need one direction consistent with the real mention signal.
    ordered = sorted([prereq, dependent], key=lambda c: c.id)
    a, b = ordered
    # Response above always says "A is prerequisite of B" i.e. a -> b.
    supported = (b.id, a.id) in mention_pairs

    fused = structure.judge_and_fuse(
        graph, [prereq, dependent], mention_pairs, {(a.id, b.id)}, sink, backend=backend
    )
    if supported:
        assert len(fused) == 1
        assert fused[0].source_id == a.id
        assert fused[0].target_id == b.id
    else:
        assert fused == []


def test_judge_and_fuse_neither_verdict_creates_no_edge(graph: Graph) -> None:
    a = _concept(graph, "a", "a mentions b")
    b = _concept(graph, "b", "b mentions a")
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps({"prerequisite": "neither", "confidence": 0.5})

    mention_pairs = structure.find_mention_pairs(graph, [a, b])
    fused = structure.judge_and_fuse(
        graph, [a, b], mention_pairs, {(a.id, b.id)}, sink, backend=backend
    )
    assert fused == []


def test_judge_and_fuse_unparseable_response_emits_diagnostic(graph: Graph) -> None:
    a = _concept(graph, "a", "a mentions b")
    b = _concept(graph, "b", "b mentions a")
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        return "garbage, not json"

    mention_pairs = structure.find_mention_pairs(graph, [a, b])
    fused = structure.judge_and_fuse(
        graph, [a, b], mention_pairs, {(a.id, b.id)}, sink, backend=backend
    )
    assert fused == []
    assert any(d.code == "judgement_parse_error" for d in sink.all())


def _candidate(source: str, target: str, confidence: float) -> EdgeCandidate:
    return EdgeCandidate(source, target, confidence, content_hash=f"{source}:{target}")


def test_break_cycles_removes_lowest_confidence_edge_in_a_triangle() -> None:
    sink = DiagnosticSink()
    candidates = [
        _candidate("A", "B", 0.9),
        _candidate("B", "C", 0.8),
        _candidate("C", "A", 0.3),  # weakest — should be removed
    ]
    surviving = structure.break_cycles(candidates, sink)
    assert ("C", "A") not in {(e.source_id, e.target_id) for e in surviving}
    assert len(surviving) == 2
    assert structure._find_cycle(surviving) is None
    assert any(d.code == "prerequisite_cycle_broken" for d in sink.all())


def test_break_cycles_leaves_acyclic_input_untouched() -> None:
    sink = DiagnosticSink()
    candidates = [_candidate("A", "B", 0.5), _candidate("B", "C", 0.5)]
    surviving = structure.break_cycles(candidates, sink)
    assert len(surviving) == 2
    assert not sink.all()


@given(
    st.lists(
        st.tuples(
            st.sampled_from("ABCDE"),
            st.sampled_from("ABCDE"),
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
        ).filter(lambda t: t[0] != t[1]),
        min_size=0,
        max_size=12,
    )
)
def test_break_cycles_property_always_yields_an_acyclic_graph(edges: list) -> None:
    sink = DiagnosticSink()
    candidates = [
        EdgeCandidate(src, tgt, conf, content_hash=f"{src}:{tgt}:{i}")
        for i, (src, tgt, conf) in enumerate(edges)
    ]
    surviving = structure.break_cycles(candidates, sink)
    assert structure._find_cycle(surviving) is None


def test_write_part_of_edges_targets_the_heading_block(graph: Graph) -> None:
    heading = graph.add(
        Block(
            created_by_pass="ingest",
            content_hash="h",
            file_id="t.pdf",
            page=0,
            bbox=[0, 0, 1, 1],
            text="1.1 Heading",
            block_type=BlockType.heading,
        )
    )
    concept = graph.add(
        Concept(
            created_by_pass="understand",
            content_hash="h",
            name="a concept",
            concept_type=ConceptType.definition,
        )
    )
    edges = structure.write_part_of_edges(graph, {concept.id: heading.id, "nonexistent": None})
    assert len(edges) == 1
    assert edges[0].kind == EdgeKind.part_of
    assert edges[0].source_id == concept.id
    assert edges[0].target_id == heading.id
    assert graph.get(edges[0].id) is not None


def test_write_prerequisite_edges_round_trips(graph: Graph) -> None:
    a = _concept(graph, "a", "a")
    b = _concept(graph, "b", "b")
    edges = structure.write_prerequisite_edges(
        graph, [EdgeCandidate(a.id, b.id, 0.7, content_hash="x")]
    )
    assert len(edges) == 1
    stored = graph.get(edges[0].id)
    assert isinstance(stored, Edge)
    assert stored.kind == EdgeKind.prerequisite_of
    assert stored.confidence == 0.7
