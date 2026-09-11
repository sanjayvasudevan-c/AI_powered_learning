import json

from conftest import add_block_with_span
from sqlmodel import select

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType, Edge, EdgeKind
from coursec.passes import compose
from coursec.passes.compose import CONTRACT_SLOTS, Dossier
from coursec.passes.understand import _span_id_for_block


def _concept(graph: Graph, name: str, definition: str) -> Concept:
    block = add_block_with_span(graph, definition)
    return graph.add(
        Concept(
            created_by_pass="understand",
            content_hash="h",
            name=name,
            concept_type=ConceptType.definition,
            definition_span_id=_span_id_for_block(graph, block.id),
        )
    )


def test_empty_dossier_refuses_without_calling_the_backend() -> None:
    dossier = Dossier(concept_id="c1", prefix="CONCEPT: x\nCOHORT: intro")  # no spans, no evidence
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        raise AssertionError("backend must never be called for an ungrounded dossier")

    result = compose.generate_slot(dossier, "definition", sink, backend=backend)

    assert result.refused is True
    assert result.refusal_reason == "empty dossier"
    assert any(d.code == "generation_refused_empty_dossier" for d in sink.all())


def test_generation_with_grounding_calls_backend_and_parses(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    from coursec.passes.dossier import build_dossier

    dossier = build_dossier(graph, concept, all_slots=CONTRACT_SLOTS)
    span_ref = f"SPAN:{concept.definition_span_id}"
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        assert span_ref in prompt  # the dossier really was passed through
        return json.dumps(
            {
                "refused": False,
                "sentences": [{"text": "Voltage drives current.", "evidence_ids": [span_ref]}],
                "mermaid": None,
            }
        )

    result = compose.generate_slot(dossier, "definition", sink, backend=backend)
    assert result.refused is False
    assert len(result.sentences) == 1
    assert result.sentences[0].evidence_ids == [span_ref]


def test_unknown_citation_is_dropped_not_trusted(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    from coursec.passes.dossier import build_dossier

    dossier = build_dossier(graph, concept, all_slots=CONTRACT_SLOTS)
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps(
            {
                "refused": False,
                "sentences": [
                    {"text": "Made up fact.", "evidence_ids": ["SPAN:not-a-real-id"]}
                ],
            }
        )

    result = compose.generate_slot(dossier, "definition", sink, backend=backend)
    assert result.sentences == []  # the only citation was fake -> sentence dropped
    assert any(d.code == "generation_unknown_citation" for d in sink.all())


def test_every_written_sentence_has_an_evidenced_by_edge(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    span_ref = f"SPAN:{concept.definition_span_id}"
    generated = compose.GeneratedSlot(
        slot="definition",
        sentences=[compose.Sentence(text="Voltage drives current.", evidence_ids=[span_ref])],
    )

    block = compose.write_lesson_block(graph, concept, generated)
    assert block is not None

    edges = [
        e
        for e in graph.session.exec(select(Edge))
        if e.kind == EdgeKind.evidenced_by and e.source_id == block.id
    ]
    cited_targets = {e.target_id for e in edges}

    content = json.loads(block.content)
    for sentence in content["sentences"]:
        for prefixed in sentence["evidence_ids"]:
            _kind, raw_id = prefixed.split(":", 1)
            assert raw_id in cited_targets, f"sentence cites {raw_id} with no evidenced_by edge"


def test_refused_slot_is_not_persisted(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    generated = compose.GeneratedSlot(slot="definition", refused=True, refusal_reason="x")
    assert compose.write_lesson_block(graph, concept, generated) is None


def test_empty_sentence_list_is_not_persisted(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    generated = compose.GeneratedSlot(slot="definition", sentences=[])
    assert compose.write_lesson_block(graph, concept, generated) is None
