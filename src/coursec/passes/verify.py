"""The verify pass — CLAUDE.md §4: owns `Verdict`, `ExecResult`; reads
`LessonBlock`.

The critic sees a `LessonBlock`'s sentences and, for each one, only the text
of *that sentence's own cited spans/evidence* — looked up fresh by id, never
the `Dossier` that produced them (PROMPTS.md D4: "[the critic] sees the
LessonBlock and only its cited spans, not the dossier" — see
test_verify.py's prompt-payload assertion). A repair pass rewrites a failing
sentence against the same citations, up to `MAX_REPAIR_ATTEMPTS` times, then
drops it. Every judgement — including ones from repair attempts, not just
the final one — is recorded as a `Verdict`. If a slot ends up with no
surviving sentence it is quarantined and its Concept marked `partial`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import (
    Block,
    Concept,
    ExecResult,
    Item,
    LessonBlock,
    SourceSpan,
    Verdict,
    WebEvidence,
)
from coursec.passes.compose import Computation, Sentence
from coursec.passes.compute import check_worked_example

PASS_NAME = "verify"
VERIFY_MODEL = "claude-haiku-4-5-20251001"  # cheap: entailment screening (CLAUDE.md §8)

MAX_REPAIR_ATTEMPTS = 2
CLASSIFICATIONS = ("entailed", "unsupported", "contradicted")

LLMBackend = Callable[[str, str, dict[str, object]], str]


def _cited_text(graph: Graph, prefixed_id: str) -> str:
    kind, _, raw_id = prefixed_id.partition(":")
    if kind == "SPAN":
        span = graph.session.get(SourceSpan, raw_id)
        if span is None:
            return ""
        block = graph.session.get(Block, span.block_id)
        return block.text.strip() if block else ""
    if kind == "EVID":
        evidence = graph.session.get(WebEvidence, raw_id)
        return evidence.chunk_text.strip() if evidence else ""
    return ""


_CRITIC_PROMPT = """You are checking whether a sentence is entailed by ONLY the cited text below. \
You have not been given any other context — judge strictly from what is here.

SENTENCE: {text}
CITED TEXT:
{cited_text}

Return ONLY a JSON object: \
{{"classification": "entailed"|"unsupported"|"contradicted"}}
"""


def build_critic_prompt(graph: Graph, sentence: Sentence) -> str:
    cited_text = "\n".join(f"- {_cited_text(graph, eid)}" for eid in sentence.evidence_ids)
    return _CRITIC_PROMPT.format(text=sentence.text, cited_text=cited_text)


def critique_sentence(
    graph: Graph, sentence: Sentence, *, backend: LLMBackend, model: str = VERIFY_MODEL
) -> str:
    from coursec.core import llm

    prompt = build_critic_prompt(graph, sentence)
    response = llm.call(model, prompt, backend=backend)
    try:
        raw = json.loads(response)
        classification = str(raw["classification"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return "unsupported"  # fail closed: never assume entailed on a parse failure
    return classification if classification in CLASSIFICATIONS else "unsupported"


_REPAIR_PROMPT = """Rewrite the sentence so it is strictly and only supported by the cited text — \
remove or correct anything the cited text does not say. Keep it concise.

ORIGINAL: {text}
CITED TEXT:
{cited_text}

Return ONLY a JSON object: {{"text": "the rewritten sentence"}}
"""


def repair_sentence(
    graph: Graph, sentence: Sentence, *, backend: LLMBackend, model: str = VERIFY_MODEL
) -> Sentence:
    from coursec.core import llm

    cited_text = "\n".join(f"- {_cited_text(graph, eid)}" for eid in sentence.evidence_ids)
    prompt = _REPAIR_PROMPT.format(text=sentence.text, cited_text=cited_text)
    response = llm.call(model, prompt, backend=backend)
    try:
        raw = json.loads(response)
        text = str(raw["text"]).strip()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return sentence  # unparseable repair: keep as-is; the next critique will likely fail it
    if not text:
        return sentence
    return Sentence(text=text, evidence_ids=sentence.evidence_ids)


def verify_lesson_block(
    graph: Graph,
    lesson_block: LessonBlock,
    sink: DiagnosticSink,
    *,
    backend: LLMBackend,
    model: str = VERIFY_MODEL,
) -> LessonBlock:
    content = json.loads(lesson_block.content)
    original = [
        Sentence(text=s["text"], evidence_ids=list(s["evidence_ids"]))
        for s in content.get("sentences", [])
    ]

    surviving: list[Sentence] = []
    for index, sentence in enumerate(original):
        current = sentence
        classification = "unsupported"
        for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
            classification = critique_sentence(graph, current, backend=backend, model=model)
            graph.add(
                Verdict(
                    created_by_pass=PASS_NAME,
                    content_hash=hashlib.sha256(
                        f"{lesson_block.id}:{index}:{attempt}:{classification}".encode()
                    ).hexdigest(),
                    lesson_block_id=lesson_block.id,
                    sentence_index=index,
                    classification=classification,
                    sentence_text=current.text,
                )
            )
            if classification == "entailed":
                surviving.append(current)
                break
            if attempt < MAX_REPAIR_ATTEMPTS:
                current = repair_sentence(graph, current, backend=backend, model=model)
        else:
            # `classification` here is always "unsupported" or "contradicted"
            # — the loop only reaches `else` when it never broke, and it only
            # breaks on "entailed". Distinct codes let callers count each
            # kind separately (CLAUDE.md D4's "unsupported-sentence count,
            # contradicted count").
            sink.emit(
                severity="warning",
                code=f"sentence_dropped_{classification}",
                message=(
                    f"sentence {index} of lesson block {lesson_block.id} dropped after "
                    f"{MAX_REPAIR_ATTEMPTS} repair attempts ({classification})"
                ),
                pass_name=PASS_NAME,
                node_id=lesson_block.id,
            )

    content["sentences"] = [
        {"text": s.text, "evidence_ids": s.evidence_ids} for s in surviving
    ]
    lesson_block.content = json.dumps(content)

    if not surviving:
        lesson_block.status = "quarantined"
        sink.emit(
            severity="warning",
            code="slot_quarantined",
            message=(
                f"slot {lesson_block.slot!r} for concept {lesson_block.concept_id} quarantined: "
                "no sentence survived verification"
            ),
            pass_name=PASS_NAME,
            node_id=lesson_block.id,
        )
        concept = graph.session.get(Concept, lesson_block.concept_id)
        if concept is not None and concept.status != "partial":
            concept.status = "partial"
            graph.session.add(concept)
    else:
        lesson_block.status = "ok"

    graph.session.add(lesson_block)
    graph.session.commit()
    return lesson_block


def _record_exec_result(
    graph: Graph,
    computation: Computation,
    sink: DiagnosticSink,
    *,
    lesson_block_id: str | None = None,
    item_id: str | None = None,
) -> ExecResult:
    """Shared by the LessonBlock and Item paths: run the computation, store
    an `ExecResult` tied to whichever one asked (exactly one of
    `lesson_block_id`/`item_id`), diagnose a divergence. D6's answer key
    reads this row rather than recomputing (PROMPTS.md D6: "not
    regenerated")."""
    result = check_worked_example(computation)
    expression = (
        f"{computation.formula} at {computation.substitutions} "
        f"claims {computation.claimed_result}"
    )
    owner_id = lesson_block_id or item_id
    exec_result = graph.add(
        ExecResult(
            created_by_pass=PASS_NAME,
            content_hash=hashlib.sha256(f"{owner_id}:{expression}".encode()).hexdigest(),
            expression=expression,
            result=json.dumps(
                {
                    "symbolic_equivalent": result.symbolic_equivalent,
                    "numeric_agreement": result.numeric_agreement,
                    "error": result.error,
                }
            ),
            success=result.agrees,
            lesson_block_id=lesson_block_id,
            item_id=item_id,
        )
    )
    if not result.agrees:
        owner_kind = "lesson block" if lesson_block_id else "item"
        sink.emit(
            severity="error",
            code="compute_divergence",
            message=(
                f"worked example in {owner_kind} {owner_id} claims "
                f"{computation.claimed_result} but {computation.formula} evaluates to "
                f"something else ({result.error or 'numeric mismatch'})"
            ),
            pass_name=PASS_NAME,
            node_id=owner_id,
        )
    return exec_result


def run_compute_check(
    graph: Graph, lesson_block: LessonBlock, sink: DiagnosticSink
) -> ExecResult | None:
    """CLAUDE.md I2: a worked example's claimed numeric result is a computed
    fact, not a generated one. No-op (returns None) if the block carries no
    `computation` — most slots don't."""
    content = json.loads(lesson_block.content)
    raw_computation = content.get("computation")
    if not raw_computation:
        return None
    computation = Computation(
        formula=raw_computation["formula"],
        substitutions=raw_computation["substitutions"],
        claimed_result=raw_computation["claimed_result"],
    )
    return _record_exec_result(graph, computation, sink, lesson_block_id=lesson_block.id)


def run_item_compute_check(graph: Graph, item: Item, sink: DiagnosticSink) -> ExecResult | None:
    """The Item-side twin of `run_compute_check` — called from assess's gate
    4 (numeric execution), but still writes through `verify` since
    `ExecResult` is verify's column, not assess's."""
    content = json.loads(item.content)
    raw_computation = content.get("computation")
    if not raw_computation:
        return None
    computation = Computation(
        formula=raw_computation["formula"],
        substitutions=raw_computation["substitutions"],
        claimed_result=raw_computation["claimed_result"],
    )
    return _record_exec_result(graph, computation, sink, item_id=item.id)
