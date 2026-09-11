import datetime as dt
import json

from conftest import add_block_with_span

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType, Edge, EdgeKind, Verdict, WebEvidence
from coursec.passes import compose, verify
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


def _lesson_block_with_one_sentence(graph: Graph, concept: Concept, cited_text_marker: str):
    span_ref = f"SPAN:{concept.definition_span_id}"
    generated = compose.GeneratedSlot(
        slot="definition",
        sentences=[
            compose.Sentence(text=f"A claim about {cited_text_marker}.", evidence_ids=[span_ref])
        ],
    )
    return compose.write_lesson_block(graph, concept, generated)


def test_critic_never_receives_dossier_content_it_did_not_cite(graph: Graph) -> None:
    # The concept has admitted evidence with a marker the sentence never
    # cites. A critic that saw the whole dossier (like compose does) would
    # leak it into its prompt; one that looks up only the cited span must not.
    concept = _concept(graph, "voltage", "Voltage relates to CITEDMARKER text.")
    uncited_evidence = graph.add(
        WebEvidence(
            created_by_pass="evidence",
            content_hash="h",
            url="https://a.edu/x",
            domain="a.edu",
            retrieved_at=dt.datetime.now(dt.UTC),
            chunk_text="UNCITEDMARKER appears only in evidence never cited by any sentence.",
            tier="tier1",
            admission="admitted",
            score=0.9,
        )
    )
    graph.add(
        Edge(
            created_by_pass="evidence",
            content_hash="e1",
            source_id=concept.id,
            target_id=uncited_evidence.id,
            kind=EdgeKind.evidenced_by,
        )
    )
    block = _lesson_block_with_one_sentence(graph, concept, "CITEDMARKER")

    captured_prompts: list[str] = []

    def backend(model: str, prompt: str, params: dict) -> str:
        captured_prompts.append(prompt)
        return json.dumps({"classification": "entailed"})

    verify.verify_lesson_block(graph, block, DiagnosticSink(), backend=backend)

    assert len(captured_prompts) == 1
    assert "CITEDMARKER" in captured_prompts[0]
    assert "UNCITEDMARKER" not in captured_prompts[0]
    assert "UNMET SLOTS" not in captured_prompts[0]  # a dossier-only marker
    assert "COHORT:" not in captured_prompts[0]  # another dossier-only marker


def test_entailed_sentence_survives_with_one_verdict(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    block = _lesson_block_with_one_sentence(graph, concept, "voltage")
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps({"classification": "entailed"})

    result = verify.verify_lesson_block(graph, block, sink, backend=backend)
    content = json.loads(result.content)
    assert len(content["sentences"]) == 1
    assert result.status == "ok"


def test_unsupported_sentence_is_repaired_up_to_max_attempts_then_dropped(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    block = _lesson_block_with_one_sentence(graph, concept, "voltage")
    sink = DiagnosticSink()
    critique_calls = []

    def backend(model: str, prompt: str, params: dict) -> str:
        if "Rewrite the sentence" in prompt:
            return json.dumps({"text": "a repaired but still unsupported sentence"})
        critique_calls.append(prompt)
        return json.dumps({"classification": "unsupported"})

    result = verify.verify_lesson_block(graph, block, sink, backend=backend)

    assert len(critique_calls) == verify.MAX_REPAIR_ATTEMPTS + 1
    content = json.loads(result.content)
    assert content["sentences"] == []
    assert result.status == "quarantined"
    assert any(d.code == "sentence_dropped_unsupported" for d in sink.all())
    assert any(d.code == "slot_quarantined" for d in sink.all())

    concept_after = graph.session.get(Concept, concept.id)
    assert concept_after.status == "partial"


def test_every_attempt_is_recorded_as_a_verdict(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    block = _lesson_block_with_one_sentence(graph, concept, "voltage")
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        if "Rewrite the sentence" in prompt:
            return json.dumps({"text": "still bad"})
        return json.dumps({"classification": "unsupported"})

    verify.verify_lesson_block(graph, block, sink, backend=backend)

    from sqlmodel import select

    verdicts = list(graph.session.exec(select(Verdict).where(Verdict.lesson_block_id == block.id)))
    assert len(verdicts) == verify.MAX_REPAIR_ATTEMPTS + 1
    assert all(v.classification == "unsupported" for v in verdicts)


def test_unparseable_critic_response_fails_closed_to_unsupported(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    block = _lesson_block_with_one_sentence(graph, concept, "voltage")

    def backend(model: str, prompt: str, params: dict) -> str:
        if "Rewrite the sentence" in prompt:
            return json.dumps({"text": "same"})
        return "not json"

    result = verify.verify_lesson_block(graph, block, DiagnosticSink(), backend=backend)
    content = json.loads(result.content)
    assert content["sentences"] == []  # never silently trusted as entailed


# --- compute check --------------------------------------------------------


def test_compute_check_agrees_with_correct_arithmetic(graph: Graph) -> None:
    concept = _concept(graph, "force", "Force equals mass times acceleration.")
    generated = compose.GeneratedSlot(
        slot="worked_example",
        sentences=[
            compose.Sentence(text="x", evidence_ids=[f"SPAN:{concept.definition_span_id}"])
        ],
        computation=compose.Computation(
            formula="m*a", substitutions={"m": 2.0, "a": 3.0}, claimed_result=6.0
        ),
    )
    block = compose.write_lesson_block(graph, concept, generated)
    sink = DiagnosticSink()
    exec_result = verify.run_compute_check(graph, block, sink)
    assert exec_result.success is True
    assert not sink.all()


def test_adversarial_wrong_constant_in_worked_example_produces_divergence(graph: Graph) -> None:
    concept = _concept(graph, "force", "Force equals mass times acceleration.")
    generated = compose.GeneratedSlot(
        slot="worked_example",
        sentences=[
            compose.Sentence(text="x", evidence_ids=[f"SPAN:{concept.definition_span_id}"])
        ],
        # 2 * 3 = 6, not 5 — a deliberately wrong constant, à la PROMPTS.md D4.
        computation=compose.Computation(
            formula="m*a", substitutions={"m": 2.0, "a": 3.0}, claimed_result=5.0
        ),
    )
    block = compose.write_lesson_block(graph, concept, generated)
    sink = DiagnosticSink()

    exec_result = verify.run_compute_check(graph, block, sink)

    assert exec_result.success is False
    divergence_diagnostics = [d for d in sink.all() if d.code == "compute_divergence"]
    assert len(divergence_diagnostics) == 1
    assert divergence_diagnostics[0].severity == "error"


def test_compute_check_is_a_noop_without_a_computation_block(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    block = _lesson_block_with_one_sentence(graph, concept, "voltage")
    assert verify.run_compute_check(graph, block, DiagnosticSink()) is None
