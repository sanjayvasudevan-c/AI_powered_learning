"""Item gates — all blocking (PROMPTS.md D5). Every surviving `Item` must
pass all four; CLAUDE.md §3: quarantine (here, outright rejection) is the
autonomous substitute for human review — never emit a failing item "with a
caveat", never loosen a gate so more pass.

**A gate that never rejects is broken, not perfect** — the fixture-based
tests exist specifically to prove each gate actually catches its case, and
the pipeline asserts the aggregate rejection rate is nonzero.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from coursec.core import llm
from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType, Item
from coursec.passes import compute, items, verify
from coursec.passes.items import GeneratedItem

# gates are screening/classification: cheap model (CLAUDE.md §8)
GATE_MODEL = "claude-haiku-4-5-20251001"

LLMBackend = Callable[[str, str, dict[str, object]], str]

LEAKAGE_CONFIDENCE_THRESHOLD = 0.8
SELF_CONSISTENCY_SAMPLES = 5


@dataclass
class GateResult:
    gate: str
    passed: bool
    reason: str


# --- Gate 1: key verification -----------------------------------------------

_SOLVE_PROMPT = """Solve this question independently, showing no work. \
STEM: {stem}
Return ONLY a JSON object: {{"answer": "..."}}
"""


def gate_key_verification(
    item: GeneratedItem,
    *,
    backend: LLMBackend,
    model: str = GATE_MODEL,
    n: int = SELF_CONSISTENCY_SAMPLES,
) -> GateResult:
    prompt = _SOLVE_PROMPT.format(stem=item.stem)
    answers = []
    for sample in range(n):
        response = llm.call(model, prompt, backend=backend, sample=sample)
        try:
            answers.append(str(json.loads(response)["answer"]).strip())
        except (json.JSONDecodeError, KeyError, TypeError):
            answers.append("")
    majority_answer, majority_count = Counter(answers).most_common(1)[0]
    passed = majority_answer == item.key.strip() and majority_count > n // 2
    return GateResult(
        gate="key_verification",
        passed=passed,
        reason=f"majority answer {majority_answer!r} ({majority_count}/{n}) vs key {item.key!r}",
    )


# --- Gate 2: leakage ---------------------------------------------------------

_NO_CONTEXT_PROMPT = """Answer this question using only general reasoning — you have been given \
no course material. Also rate your confidence.
STEM: {stem}
Return ONLY a JSON object: {{"answer": "...", "confidence": <float 0-1>}}
"""


def gate_leakage(
    item: GeneratedItem, *, backend: LLMBackend, model: str = GATE_MODEL
) -> GateResult:
    prompt = _NO_CONTEXT_PROMPT.format(stem=item.stem)
    response = llm.call(model, prompt, backend=backend)
    try:
        raw = json.loads(response)
        answer = str(raw["answer"]).strip()
        confidence = float(raw["confidence"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return GateResult(
            gate="leakage", passed=True, reason="unparseable no-context response, not a leak"
        )

    correct = answer == item.key.strip()
    confident = confidence >= LEAKAGE_CONFIDENCE_THRESHOLD
    leaked = correct and confident
    return GateResult(
        gate="leakage",
        passed=not leaked,
        reason=(
            f"answered {'correctly' if correct else 'incorrectly'} with no context "
            f"(confidence {confidence:.2f})"
        ),
    )


# --- Gate 3: single answer ---------------------------------------------------

_DEFENSIBLY_WRONG_PROMPT = """QUESTION: {stem}
PROPOSED ANSWER: {distractor}

Is the proposed answer defensibly WRONG given the question, or could a reasonable person \
argue it is also correct?
Return ONLY a JSON object: {{"defensibly_wrong": true|false}}
"""


def gate_single_answer(
    item: GeneratedItem, *, backend: LLMBackend, model: str = GATE_MODEL
) -> GateResult:
    if item.item_type != "mcq":
        return GateResult(gate="single_answer", passed=True, reason="not an MCQ")
    for distractor in item.distractors:
        prompt = _DEFENSIBLY_WRONG_PROMPT.format(stem=item.stem, distractor=distractor.text)
        response = llm.call(model, prompt, backend=backend)
        try:
            defensibly_wrong = bool(json.loads(response)["defensibly_wrong"])
        except (json.JSONDecodeError, KeyError, TypeError):
            defensibly_wrong = False  # fail closed: an unparseable judgement is not a pass
        if not defensibly_wrong:
            return GateResult(
                gate="single_answer",
                passed=False,
                reason=f"distractor {distractor.text!r} is not defensibly wrong — ambiguous item",
            )
    return GateResult(gate="single_answer", passed=True, reason="every distractor defensibly wrong")


# --- Gate 4: numeric execution -----------------------------------------------


def gate_numeric_execution(item: GeneratedItem) -> GateResult:
    if item.item_type != "numeric":
        return GateResult(gate="numeric_execution", passed=True, reason="not a numeric item")
    if item.computation is None:
        return GateResult(
            gate="numeric_execution",
            passed=False,
            reason="numeric item has no computation to check",
        )
    result = compute.check_worked_example(item.computation)
    if not result.agrees:
        return GateResult(
            gate="numeric_execution",
            passed=False,
            reason=f"key does not match sandbox result ({result.error or 'mismatch'})",
        )
    return GateResult(gate="numeric_execution", passed=True, reason="key matches sandbox result")


# --- Orchestration ------------------------------------------------------------


def run_gates(
    item: GeneratedItem, *, backend: LLMBackend, model: str = GATE_MODEL
) -> list[GateResult]:
    """All four gates, always run in full (not short-circuited) so a
    rejection log shows every reason an item failed, not just the first."""
    return [
        gate_key_verification(item, backend=backend, model=model),
        gate_leakage(item, backend=backend, model=model),
        gate_single_answer(item, backend=backend, model=model),
        gate_numeric_execution(item),
    ]


def gates_pass(results: list[GateResult]) -> bool:
    return all(r.passed for r in results)


ITEM_TYPE_BY_CONCEPT_TYPE: dict[ConceptType, str] = {
    ConceptType.definition: "mcq",
    ConceptType.formula: "numeric",
    ConceptType.procedure: "derivation",
    ConceptType.theorem: "short",
    ConceptType.phenomenon: "short",
    ConceptType.example: "application",
}


def _definition_text(graph: Graph, concept: Concept) -> str:
    from coursec.core.models import Block, SourceSpan

    if concept.definition_span_id is None:
        return ""
    span = graph.session.get(SourceSpan, concept.definition_span_id)
    if span is None:
        return ""
    block = graph.session.get(Block, span.block_id)
    return block.text.strip() if block else ""


def assess_concept(
    graph: Graph,
    concept: Concept,
    sink: DiagnosticSink,
    *,
    backend: LLMBackend,
    item_model: str = items.ITEM_MODEL,
    gate_model: str = GATE_MODEL,
    bloom_levels: tuple[str, ...] = items.BLOOM_LEVELS,
) -> tuple[list[Item], int]:
    """Generate one item per Bloom level, gate each, persist only what
    survives. Returns (accepted_items, rejected_count)."""
    definition_text = _definition_text(graph, concept)
    misconceptions = items.gather_misconception_pool(
        graph, concept, definition_text, sink, backend=backend
    )
    item_type = ITEM_TYPE_BY_CONCEPT_TYPE.get(concept.concept_type, "short")

    accepted: list[Item] = []
    rejected_count = 0
    for bloom_level in bloom_levels:
        generated = items.generate_item(
            concept, definition_text, bloom_level, item_type, misconceptions, sink,
            backend=backend, model=item_model,
        )
        if generated.refused:
            sink.emit(
                severity="info",
                code="item_generation_refused",
                message=(
                    f"item refused for {concept.name!r} at {bloom_level}: "
                    f"{generated.refusal_reason}"
                ),
                pass_name=items.PASS_NAME,
            )
            continue

        gate_results = run_gates(generated, backend=backend, model=gate_model)
        if not gates_pass(gate_results):
            rejected_count += 1
            failed = [r for r in gate_results if not r.passed]
            sink.emit(
                severity="warning",
                code="item_rejected",
                message=(
                    f"item for {concept.name!r} at {bloom_level} rejected: "
                    + "; ".join(f"{r.gate}: {r.reason}" for r in failed)
                ),
                pass_name=items.PASS_NAME,
            )
            continue

        item = items.write_item(graph, concept, bloom_level, generated)
        if item is None:
            rejected_count += 1
            sink.emit(
                severity="warning",
                code="item_rejected",
                message=(
                    f"item for {concept.name!r} at {bloom_level} rejected: "
                    "no persistable content"
                ),
                pass_name=items.PASS_NAME,
            )
            continue
        if item.item_type == "numeric":
            # Gate 4 already checked this in memory; persist the same
            # computation as a real ExecResult now that the Item has an id,
            # so D6's answer key can read it instead of recomputing.
            verify.run_item_compute_check(graph, item, sink)
        accepted.append(item)

    return accepted, rejected_count
