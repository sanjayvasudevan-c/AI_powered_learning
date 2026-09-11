"""The compose pass — CLAUDE.md §4: owns `LessonBlock`; reads dossiers.

One LLM call per contract slot (CLAUDE.md I5's first four of five MVP
slots — the fifth, >=2 items, is D5's), constrained to the Concept's
`Dossier`. Output is structured: each sentence carries the evidence ids it
used, so verify.py's critic can check exactly those and nothing else, and
the origin linter can trace every number. Visuals are Mermaid source, never
prose describing an image. A refusal is a valid, plumbed-through outcome —
never swallowed.

**Grounding is enforced in code, not just asked of the model**: `generate_slot`
refuses before ever calling the backend if the dossier has no citable
content (`Dossier.has_grounding`), so "no dossier -> no generation" is a
hard invariant this project can test without a live model, not a hope about
what a real one would do (CLAUDE.md I1: no unsupported sentence).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from coursec.core import llm
from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Concept, Edge, EdgeKind, LessonBlock
from coursec.passes.dossier import Dossier, build_dossier

PASS_NAME = "compose"
COMPOSE_MODEL = "claude-sonnet-5"  # strong model: synthesis (CLAUDE.md §8)

CONTRACT_SLOTS: tuple[str, ...] = (
    "definition",
    "intuition",
    "worked_example",
    "visual_or_analogy",
)

LLMBackend = Callable[[str, str, dict[str, object]], str]


@dataclass
class Sentence:
    text: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class Computation:
    """A worked example's numeric claim, structured rather than prose, so
    compute.py can mechanically verify it instead of parsing English."""

    formula: str  # e.g. "m*a"
    substitutions: dict[str, float]
    claimed_result: float


@dataclass
class GeneratedSlot:
    slot: str
    sentences: list[Sentence] = field(default_factory=list)
    mermaid: str | None = None
    computation: Computation | None = None
    refused: bool = False
    refusal_reason: str | None = None


_GENERATION_PROMPT = """You are writing the "{slot}" section of a lesson on the concept below, \
for a course-compiler pipeline. You may use ONLY the material in DOSSIER — \
never your own outside knowledge. Every sentence you write must cite the \
bracketed id(s) — [SPAN:...] or [EVID:...] — of the dossier line(s) it is \
drawn from.

DOSSIER:
{prefix}

If the dossier does not contain enough material to write this section \
honestly, refuse rather than filling the gap from memory.

Return ONLY a JSON object (no prose, no markdown fence):
{{"refused": false, \
"sentences": [{{"text": "...", "evidence_ids": ["SPAN:..." or "EVID:..."]}}, ...], \
"mermaid": "<Mermaid source, ONLY for a visual_or_analogy slot, else null>", \
"computation": {{"formula": "...", "substitutions": {{"var": 1.0}}, "claimed_result": 1.0}} \
or null (ONLY a worked_example slot with an actual numeric claim needs this — \
it lets compute.py verify the arithmetic instead of trusting the prose)}}
or
{{"refused": true, "reason": "..."}}
"""


def _build_prompt(dossier: Dossier, slot: str) -> str:
    return _GENERATION_PROMPT.format(slot=slot, prefix=dossier.prefix)


def _valid_evidence_ids(dossier: Dossier) -> set[str]:
    return {f"SPAN:{i}" for i in dossier.source_span_ids} | {
        f"EVID:{i}" for i in dossier.evidence_ids
    }


def _parse_response(
    response: str, slot: str, dossier: Dossier, sink: DiagnosticSink
) -> GeneratedSlot:
    try:
        raw = json.loads(response)
    except json.JSONDecodeError:
        sink.emit(
            severity="warning",
            code="generation_parse_error",
            message=f"could not parse generation response for slot {slot!r}: {response[:200]!r}",
            pass_name=PASS_NAME,
        )
        return GeneratedSlot(slot=slot, refused=True, refusal_reason="unparseable response")

    if raw.get("refused"):
        return GeneratedSlot(slot=slot, refused=True, refusal_reason=raw.get("reason", "refused"))

    valid_ids = _valid_evidence_ids(dossier)
    sentences: list[Sentence] = []
    for item in raw.get("sentences", []):
        text = str(item.get("text", "")).strip()
        cited = [str(e) for e in item.get("evidence_ids", [])]
        unknown = [e for e in cited if e not in valid_ids]
        if unknown:
            sink.emit(
                severity="warning",
                code="generation_unknown_citation",
                message=f"sentence cited {unknown!r}, not present in the dossier — dropped",
                pass_name=PASS_NAME,
            )
            cited = [e for e in cited if e in valid_ids]
        if not text or not cited:
            # I1: a sentence with no surviving citation is unsupported by
            # construction — it is never emitted in the first place.
            if text:
                sink.emit(
                    severity="warning",
                    code="generation_uncited_sentence",
                    message=f"dropped an uncited sentence in slot {slot!r}: {text[:80]!r}",
                    pass_name=PASS_NAME,
                )
            continue
        sentences.append(Sentence(text=text, evidence_ids=cited))

    mermaid = raw.get("mermaid") if slot == "visual_or_analogy" else None

    computation = None
    raw_computation = raw.get("computation") if slot == "worked_example" else None
    if isinstance(raw_computation, dict):
        try:
            computation = Computation(
                formula=str(raw_computation["formula"]),
                substitutions={
                    str(k): float(v) for k, v in raw_computation["substitutions"].items()
                },
                claimed_result=float(raw_computation["claimed_result"]),
            )
        except (KeyError, TypeError, ValueError):
            sink.emit(
                severity="warning",
                code="generation_malformed_computation",
                message=f"dropped malformed computation block: {raw_computation!r}",
                pass_name=PASS_NAME,
            )

    return GeneratedSlot(slot=slot, sentences=sentences, mermaid=mermaid, computation=computation)


def generate_slot(
    dossier: Dossier,
    slot: str,
    sink: DiagnosticSink,
    *,
    backend: LLMBackend,
    model: str = COMPOSE_MODEL,
) -> GeneratedSlot:
    if not dossier.has_grounding:
        sink.emit(
            severity="info",
            code="generation_refused_empty_dossier",
            message=f"slot {slot!r} refused: dossier for {dossier.concept_id} has no grounding",
            pass_name=PASS_NAME,
        )
        return GeneratedSlot(slot=slot, refused=True, refusal_reason="empty dossier")

    prompt = _build_prompt(dossier, slot)
    response = llm.call(model, prompt, backend=backend)
    return _parse_response(response, slot, dossier, sink)


def _resolve_evidence_id(prefixed: str) -> tuple[str, str]:
    """"SPAN:xyz" -> ("SPAN", "xyz")."""
    kind, _, raw_id = prefixed.partition(":")
    return kind, raw_id


def write_lesson_block(
    graph: Graph, concept: Concept, generated: GeneratedSlot, *, status: str = "ok"
) -> LessonBlock | None:
    """Persist a non-refused, non-empty slot as a LessonBlock, plus one
    `evidenced_by` edge per distinct citation (decision log #9)."""
    if generated.refused or not generated.sentences:
        return None

    content = json.dumps(
        {
            "sentences": [
                {"text": s.text, "evidence_ids": s.evidence_ids} for s in generated.sentences
            ],
            "mermaid": generated.mermaid,
            "computation": (
                {
                    "formula": generated.computation.formula,
                    "substitutions": generated.computation.substitutions,
                    "claimed_result": generated.computation.claimed_result,
                }
                if generated.computation
                else None
            ),
        }
    )
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    block = graph.add(
        LessonBlock(
            created_by_pass=PASS_NAME,
            content_hash=content_hash,
            concept_id=concept.id,
            slot=generated.slot,
            content=content,
            status=status,
        )
    )

    cited_ids = {eid for s in generated.sentences for eid in s.evidence_ids}
    for prefixed in sorted(cited_ids):
        _kind, raw_id = _resolve_evidence_id(prefixed)
        graph.add(
            Edge(
                created_by_pass=PASS_NAME,
                content_hash=hashlib.sha256(f"{block.id}:{raw_id}:evidenced_by".encode()).hexdigest(),
                source_id=block.id,
                target_id=raw_id,
                kind=EdgeKind.evidenced_by,
            )
        )
    return block


def compose_concept(
    graph: Graph,
    concept: Concept,
    sink: DiagnosticSink,
    *,
    backend: LLMBackend,
    model: str = COMPOSE_MODEL,
    cohort_level: str = "intro",
    slots: tuple[str, ...] = CONTRACT_SLOTS,
) -> list[LessonBlock]:
    written: list[LessonBlock] = []
    for slot in slots:
        dossier = build_dossier(graph, concept, all_slots=slots, cohort_level=cohort_level)
        generated = generate_slot(dossier, slot, sink, backend=backend, model=model)
        block = write_lesson_block(graph, concept, generated)
        if block is not None:
            written.append(block)
    return written
