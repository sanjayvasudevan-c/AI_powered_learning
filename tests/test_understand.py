import json
from pathlib import Path

from conftest import add_block_with_span
from sqlmodel import select

from coursec.core import embeddings
from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Alias, Block, BlockType, ConceptType
from coursec.passes import understand
from coursec.passes.understand import Candidate

MERGE_LABELS = json.loads(
    (Path(__file__).parent / "fixtures" / "concept_merge_labels.json").read_text()
)


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


def _candidates_from_labels(graph: Graph) -> dict[str, Candidate]:
    candidates = {}
    for key, info in MERGE_LABELS["concepts"].items():
        block = add_block_with_span(graph, info["definition"])
        span_id = understand._span_id_for_block(graph, block.id)
        candidates[key] = Candidate(
            name=info["name"],
            concept_type=ConceptType.definition,
            salience=0.5,
            definition_span_id=span_id,
            definition_text=info["definition"],
            section_heading_id=None,
        )
    return candidates


def test_chunk_by_section_groups_at_heading_boundaries(graph: Graph) -> None:
    heading_a = _heading(graph, "1.1 Section A")
    para_a1 = add_block_with_span(graph, "content a1")
    para_a2 = add_block_with_span(graph, "content a2")
    heading_b = _heading(graph, "1.2 Section B")
    para_b1 = add_block_with_span(graph, "content b1")

    sections = understand.chunk_by_section([heading_a, para_a1, para_a2, heading_b, para_b1])

    assert len(sections) == 2
    assert sections[0].heading is heading_a
    assert sections[0].blocks == [para_a1, para_a2]
    assert sections[1].heading is heading_b
    assert sections[1].blocks == [para_b1]


def test_chunk_by_section_keeps_leading_content_with_no_heading(graph: Graph) -> None:
    preamble = add_block_with_span(graph, "no heading yet")
    heading = _heading(graph, "1.1 Later")
    sections = understand.chunk_by_section([preamble, heading])
    assert sections[0].heading is None
    assert sections[0].blocks == [preamble]


def test_merge_threshold_separates_labelled_pairs(graph: Graph) -> None:
    candidates_by_key = _candidates_from_labels(graph)
    concepts, _ = understand.canonicalize(list(candidates_by_key.values()), graph)

    concept_id_by_name: dict[str, str] = {c.name: c.id for c in concepts}
    for alias in graph.session.exec(select(Alias)):
        concept_id_by_name[alias.alias_text] = alias.concept_id

    names = {key: info["name"] for key, info in MERGE_LABELS["concepts"].items()}

    for key_a, key_b in MERGE_LABELS["should_merge"]:
        name_a, name_b = names[key_a], names[key_b]
        assert concept_id_by_name[name_a] == concept_id_by_name[name_b], (
            f"{name_a!r} and {name_b!r} should have merged into one Concept"
        )

    for key_a, key_b in MERGE_LABELS["should_not_merge"]:
        name_a, name_b = names[key_a], names[key_b]
        assert concept_id_by_name[name_a] != concept_id_by_name[name_b], (
            f"{name_a!r} and {name_b!r} should NOT have merged"
        )


def test_no_two_surviving_concepts_exceed_merge_threshold(graph: Graph) -> None:
    candidates_by_key = _candidates_from_labels(graph)
    concepts, _ = understand.canonicalize(list(candidates_by_key.values()), graph)

    # Re-embed exactly what clustering saw (name: definition) for each
    # survivor and check no pair exceeds the threshold that would have
    # merged them.
    definition_by_name = {
        info["name"]: info["definition"] for info in MERGE_LABELS["concepts"].values()
    }
    texts = [f"{c.name}: {definition_by_name[c.name]}" for c in concepts]
    vectors = embeddings.embed(texts)
    similarity = vectors @ vectors.T

    for i in range(len(concepts)):
        for j in range(i + 1, len(concepts)):
            assert similarity[i, j] < understand.MERGE_SIMILARITY_THRESHOLD


FAKE_EXTRACTION_RESPONSE = json.dumps(
    [{"name": "Ohm's law", "type": "formula", "salience": 0.9, "source_index": 0}]
)


def test_extract_candidates_links_span_from_scripted_backend(graph: Graph) -> None:
    heading = _heading(graph, "1.1 Circuits")
    para = add_block_with_span(graph, "Ohm's law relates voltage, current, and resistance.")
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        return FAKE_EXTRACTION_RESPONSE

    candidates = understand.extract_candidates([heading, para], graph, sink, backend=backend)

    assert len(candidates) == 1
    assert candidates[0].name == "Ohm's law"
    assert candidates[0].section_heading_id == heading.id
    assert candidates[0].definition_span_id == understand._span_id_for_block(graph, para.id)
    assert not sink.all()


def test_malformed_llm_response_emits_diagnostic_and_drops_candidate(graph: Graph) -> None:
    heading = _heading(graph, "1.1 Circuits")
    para = add_block_with_span(graph, "some text")
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        return "not json at all"

    candidates = understand.extract_candidates([heading, para], graph, sink, backend=backend)
    assert candidates == []
    assert any(d.code == "candidate_parse_error" for d in sink.all())


def test_canonicalize_empty_input_returns_empty(graph: Graph) -> None:
    concepts, sections = understand.canonicalize([], graph)
    assert concepts == []
    assert sections == {}
