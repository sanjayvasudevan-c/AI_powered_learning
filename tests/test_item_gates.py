import json

from conftest import add_block_with_span

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType
from coursec.passes import item_gates
from coursec.passes.compose import Computation
from coursec.passes.items import Distractor, GeneratedItem
from coursec.passes.understand import _span_id_for_block


def test_gate_key_verification_passes_on_majority_match() -> None:
    item = GeneratedItem(item_type="short", stem="What is 2+2?", key="4")

    def backend(model: str, prompt: str, params: dict) -> str:
        # majority says "4" (matches key); one dissenter
        return json.dumps({"answer": "4" if params.get("sample", 0) != 4 else "5"})

    result = item_gates.gate_key_verification(item, backend=backend)
    assert result.passed is True


def test_gate_key_verification_fails_on_no_majority_match() -> None:
    item = GeneratedItem(item_type="short", stem="What is 2+2?", key="4")

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps({"answer": "5"})  # never matches the key

    result = item_gates.gate_key_verification(item, backend=backend)
    assert result.passed is False


def test_gate_leakage_rejects_an_item_answerable_with_no_context() -> None:
    # Fixture: "what is 2+2" needs no course material at all — trivia, per
    # PROMPTS.md D5's exact framing of this gate.
    item = GeneratedItem(item_type="short", stem="What is 2 + 2?", key="4")

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps({"answer": "4", "confidence": 0.99})

    result = item_gates.gate_leakage(item, backend=backend)
    assert result.passed is False


def test_gate_leakage_accepts_an_item_the_model_cannot_confidently_answer_cold() -> None:
    item = GeneratedItem(
        item_type="short",
        stem="What does Section 4.2 of this chapter define X as?",
        key="the source's specific definition",
    )

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps({"answer": "not sure", "confidence": 0.1})

    result = item_gates.gate_leakage(item, backend=backend)
    assert result.passed is True


def test_gate_single_answer_rejects_an_item_with_two_defensible_answers() -> None:
    # Fixture: a distractor that is ALSO arguably correct.
    item = GeneratedItem(
        item_type="mcq",
        stem="Which unit measures electric potential difference?",
        key="volt",
        distractors=[Distractor(text="volt (also technically correct)", misconception_id="m0")],
    )

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps({"defensibly_wrong": False})

    result = item_gates.gate_single_answer(item, backend=backend)
    assert result.passed is False


def test_gate_single_answer_accepts_when_every_distractor_is_defensibly_wrong() -> None:
    item = GeneratedItem(
        item_type="mcq",
        stem="Which unit measures electric potential difference?",
        key="volt",
        distractors=[Distractor(text="ampere", misconception_id="m0")],
    )

    def backend(model: str, prompt: str, params: dict) -> str:
        return json.dumps({"defensibly_wrong": True})

    result = item_gates.gate_single_answer(item, backend=backend)
    assert result.passed is True


def test_gate_numeric_execution_passes_correct_computation() -> None:
    item = GeneratedItem(
        item_type="numeric", stem="x", key="6.0",
        computation=Computation(
            formula="m*a", substitutions={"m": 2.0, "a": 3.0}, claimed_result=6.0
        ),
    )
    assert item_gates.gate_numeric_execution(item).passed is True


def test_gate_numeric_execution_fails_wrong_computation() -> None:
    item = GeneratedItem(
        item_type="numeric", stem="x", key="5.0",
        computation=Computation(
            formula="m*a", substitutions={"m": 2.0, "a": 3.0}, claimed_result=5.0
        ),
    )
    assert item_gates.gate_numeric_execution(item).passed is False


def test_gate_numeric_execution_fails_without_a_computation() -> None:
    item = GeneratedItem(item_type="numeric", stem="x", key="5.0")
    assert item_gates.gate_numeric_execution(item).passed is False


def test_non_mcq_non_numeric_items_pass_gates_3_and_4_trivially() -> None:
    item = GeneratedItem(item_type="short", stem="x", key="y")
    assert item_gates.gate_single_answer(item, backend=lambda *a: "").passed is True
    assert item_gates.gate_numeric_execution(item).passed is True


def test_run_gates_runs_all_four_even_after_an_early_failure() -> None:
    item = GeneratedItem(
        item_type="mcq", stem="What is 2 + 2?", key="4",
        distractors=[Distractor(text="also arguably 4", misconception_id="m0")],
    )
    calls = {"leakage": 0, "single_answer": 0}

    def backend(model: str, prompt: str, params: dict) -> str:
        if "defensibly wrong" in prompt.lower() or "defensibly_wrong" in prompt:
            calls["single_answer"] += 1
            return json.dumps({"defensibly_wrong": False})
        if "no course material" in prompt:
            calls["leakage"] += 1
            return json.dumps({"answer": "4", "confidence": 0.99})
        return json.dumps({"answer": "4"})

    results = item_gates.run_gates(item, backend=backend)
    assert len(results) == 4
    assert calls["leakage"] == 1  # ran even though the earlier gate already failed
    assert calls["single_answer"] == 1
    assert item_gates.gates_pass(results) is False


def test_assess_concept_rejection_rate_is_nonzero(graph: Graph) -> None:
    # A definition concept generates "mcq" items at every Bloom level; one
    # of them ("apply") gets a leaky, trivially-answerable stem so at least
    # one item is guaranteed to be rejected — proving the aggregate
    # rejection rate really can be nonzero, not just asserting it blindly.
    block = add_block_with_span(graph, "Voltage is electric potential difference.")
    concept = graph.add(
        Concept(
            created_by_pass="understand", content_hash="h", name="voltage",
            concept_type=ConceptType.definition,
            definition_span_id=_span_id_for_block(graph, block.id),
        )
    )
    sink = DiagnosticSink()

    def backend(model: str, prompt: str, params: dict) -> str:
        if "misconceptions" in prompt.lower() and "List" in prompt:
            return json.dumps(["confuses voltage with current"])
        if "Write one mcq" in prompt and "apply" in prompt:
            # A leaky item: trivia answerable with no context at all.
            return json.dumps(
                {"stem": "What is 2 + 2?", "key": "4",
                 "distractors": [{"text": "5", "misconception_index": 0}]}
            )
        if "Write one mcq" in prompt:
            return json.dumps(
                {"stem": "What is voltage?", "key": "Electric potential difference",
                 "distractors": [{"text": "Electric current", "misconception_index": 0}]}
            )
        if "no course material" in prompt:
            # leakage gate: answer "What is 2+2?" confidently; anything else, don't know
            if "2 + 2" in prompt:
                return json.dumps({"answer": "4", "confidence": 0.99})
            return json.dumps({"answer": "unsure", "confidence": 0.1})
        if "defensibly_wrong" in prompt or "defensibly WRONG" in prompt:
            return json.dumps({"defensibly_wrong": True})
        if "Solve this question" in prompt:
            if "2 + 2" in prompt:
                return json.dumps({"answer": "4"})
            return json.dumps({"answer": "Electric potential difference"})
        return json.dumps({"refused": True, "reason": "unhandled prompt"})

    accepted, rejected_count = item_gates.assess_concept(graph, concept, sink, backend=backend)

    assert rejected_count > 0
    assert any(d.code == "item_rejected" for d in sink.all())
    # And the gate that actually caught it was leakage, on the leaky item.
    leaky_rejection = next(d for d in sink.all() if d.code == "item_rejected")
    assert "leakage" in leaky_rejection.message
