import json

from conftest import add_block_with_span
from sqlmodel import select

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import (
    Concept,
    ConceptType,
    Edge,
    EdgeKind,
    LessonBlock,
    Verdict,
)
from coursec.passes import items
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


def test_harvest_contradicted_misconceptions_uses_real_verdict_text(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    block = graph.add(
        LessonBlock(
            created_by_pass="compose", content_hash="h", concept_id=concept.id,
            slot="definition", content="{}",
        )
    )
    graph.add(
        Verdict(
            created_by_pass="verify", content_hash="v1", lesson_block_id=block.id,
            sentence_index=0, classification="contradicted",
            sentence_text="Voltage is measured in amperes.",
        )
    )
    graph.add(
        Verdict(
            created_by_pass="verify", content_hash="v2", lesson_block_id=block.id,
            sentence_index=1, classification="entailed", sentence_text="irrelevant",
        )
    )

    descriptions = items.harvest_contradicted_misconceptions(graph, concept)
    assert len(descriptions) == 1
    assert "Voltage is measured in amperes." in descriptions[0]


def test_elicit_misconceptions_parses_scripted_backend(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps(["confuses voltage with current", "thinks voltage is a substance"])

    result = items.elicit_misconceptions(concept, "def text", backend=backend)
    assert result == ["confuses voltage with current", "thinks voltage is a substance"]


def test_gather_misconception_pool_reuses_existing(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    items.write_misconceptions(graph, concept, ["pre-existing misconception"])
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        raise AssertionError("must not elicit again when misconceptions already exist")

    pool = items.gather_misconception_pool(graph, concept, "def", sink, backend=backend)
    assert len(pool) == 1
    assert pool[0].description == "pre-existing misconception"


def test_mcq_distractor_without_misconception_is_dropped(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    misconceptions = items.write_misconceptions(graph, concept, ["m0"])
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps(
            {
                "stem": "What is voltage?",
                "key": "Electric potential difference",
                "distractors": [
                    {"text": "valid distractor", "misconception_index": 0},
                    {"text": "invalid distractor", "misconception_index": 99},
                ],
            }
        )

    generated = items.generate_item(
        concept, "def", "remember", "mcq", misconceptions, sink, backend=backend
    )
    assert len(generated.distractors) == 1
    assert generated.distractors[0].text == "valid distractor"
    assert any(d.code == "distractor_missing_misconception" for d in sink.all())


def test_every_written_mcq_distractor_has_a_misconception_edge(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    misconceptions = items.write_misconceptions(graph, concept, ["m0", "m1"])
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps(
            {
                "stem": "What is voltage?",
                "key": "Electric potential difference",
                "distractors": [
                    {"text": "d0", "misconception_index": 0},
                    {"text": "d1", "misconception_index": 1},
                ],
            }
        )

    generated = items.generate_item(
        concept, "def", "remember", "mcq", misconceptions, sink, backend=backend
    )
    item = items.write_item(graph, concept, "remember", generated)
    assert item is not None

    assesses_edges = [
        e
        for e in graph.session.exec(select(Edge))
        if e.kind == EdgeKind.assesses and e.source_id == item.id
    ]
    targets = {e.target_id for e in assesses_edges}
    assert concept.id in targets
    for m in misconceptions:
        assert m.id in targets


def test_mcq_with_zero_surviving_distractors_is_not_persisted(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps(
            {
                "stem": "What is voltage?",
                "key": "x",
                # no real misconceptions exist
                "distractors": [{"text": "d0", "misconception_index": 0}],
            }
        )

    generated = items.generate_item(concept, "def", "remember", "mcq", [], sink, backend=backend)
    assert items.write_item(graph, concept, "remember", generated) is None


def test_refused_item_is_not_persisted(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps({"refused": True, "reason": "insufficient material"})

    generated = items.generate_item(concept, "def", "remember", "short", [], sink, backend=backend)
    assert generated.refused is True
    assert items.write_item(graph, concept, "remember", generated) is None


def test_generate_item_refuses_without_source_material(graph: Graph) -> None:
    concept = graph.add(
        Concept(
            created_by_pass="understand", content_hash="h", name="ungrounded",
            concept_type=ConceptType.definition,
        )
    )
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        raise AssertionError("must never call the backend with no source material")

    generated = items.generate_item(concept, "", "remember", "short", [], sink, backend=backend)
    assert generated.refused is True
