"""The items pass — CLAUDE.md §4: owns `Item`, `Misconception` (and, once
the pilot runs, `ItemStats`); reads `Concept`, `LessonBlock`.

Per Concept x Bloom level, one item. **Every MCQ distractor must link to a
Misconception node** naming the specific faulty reasoning it encodes — this
is what makes D7's root-cause readout diagnostic rather than merely
adaptive (PROMPTS.md D5), so it is never shortcut. Misconceptions are
sourced from two places: sentences D4's Critic actually marked
"contradicted" (real material, not invented), and an explicit elicitation
call. A distractor citing an unsourced misconception is dropped rather than
kept with a fabricated link; an MCQ item left with zero surviving
distractors is rejected outright (`write_item` returns `None`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlmodel import select

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import (
    Concept,
    Edge,
    EdgeKind,
    Item,
    LessonBlock,
    Misconception,
    Verdict,
)
from coursec.passes.compose import Computation

PASS_NAME = "assess"
ITEM_MODEL = "claude-sonnet-5"  # item authoring: strong model (CLAUDE.md §8)

BLOOM_LEVELS: tuple[str, ...] = ("remember", "apply", "analyze")
ITEM_TYPES = ("mcq", "numeric", "short", "derivation", "application")

LLMBackend = Callable[[str, str, dict[str, object]], str]


# ---------------------------------------------------------------------------
# Misconception sourcing
# ---------------------------------------------------------------------------


def harvest_contradicted_misconceptions(graph: Graph, concept: Concept) -> list[str]:
    """Descriptions drawn from real D4 Verdict rows the Critic classified
    "contradicted", for this concept's own LessonBlocks."""
    blocks = graph.session.exec(
        select(LessonBlock).where(LessonBlock.concept_id == concept.id)
    ).all()
    block_ids = {b.id for b in blocks}
    if not block_ids:
        return []
    verdicts = graph.session.exec(
        select(Verdict).where(Verdict.classification == "contradicted")
    ).all()
    return [
        f"Believing '{v.sentence_text}' — this was contradicted by the cited source/evidence."
        for v in verdicts
        if v.lesson_block_id in block_ids and v.sentence_text
    ]


_ELICITATION_PROMPT = """List {n} distinct, specific misconceptions a student might hold about \
the concept below. Each must name the exact faulty reasoning (e.g. "confuses X with Y \
because ..."), not a vague "doesn't understand X".

CONCEPT: {name} ({concept_type})
DEFINITION: {definition}

Return ONLY a JSON array of strings.
"""


def elicit_misconceptions(
    concept: Concept,
    definition_text: str,
    *,
    backend: LLMBackend,
    model: str = ITEM_MODEL,
    n: int = 2,
) -> list[str]:
    from coursec.core import llm

    prompt = _ELICITATION_PROMPT.format(
        n=n, name=concept.name, concept_type=concept.concept_type.value, definition=definition_text
    )
    response = llm.call(model, prompt, backend=backend)
    try:
        raw = json.loads(response)
        if not isinstance(raw, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def write_misconceptions(
    graph: Graph, concept: Concept, descriptions: list[str]
) -> list[Misconception]:
    written = []
    for description in descriptions:
        written.append(
            graph.add(
                Misconception(
                    created_by_pass=PASS_NAME,
                    content_hash=hashlib.sha256(description.encode()).hexdigest(),
                    concept_id=concept.id,
                    description=description,
                )
            )
        )
    return written


def gather_misconception_pool(
    graph: Graph,
    concept: Concept,
    definition_text: str,
    sink: DiagnosticSink,
    *,
    backend: LLMBackend,
) -> list[Misconception]:
    """The full set of real Misconception nodes MCQ distractors for this
    concept are allowed to cite."""
    existing = graph.session.exec(
        select(Misconception).where(Misconception.concept_id == concept.id)
    ).all()
    if existing:
        return list(existing)

    descriptions = harvest_contradicted_misconceptions(graph, concept)
    descriptions += elicit_misconceptions(concept, definition_text, backend=backend)
    if not descriptions:
        sink.emit(
            severity="info",
            code="no_misconceptions_available",
            message=f"no misconception source material for concept {concept.name!r}",
            pass_name=PASS_NAME,
        )
    return write_misconceptions(graph, concept, descriptions)


# ---------------------------------------------------------------------------
# Item generation
# ---------------------------------------------------------------------------


@dataclass
class Distractor:
    text: str
    misconception_id: str


@dataclass
class GeneratedItem:
    item_type: str
    stem: str = ""
    key: str = ""
    distractors: list[Distractor] = field(default_factory=list)
    computation: Computation | None = None
    refused: bool = False
    refusal_reason: str | None = None


_ITEM_PROMPT = """Write one {item_type} assessment item testing "{name}" at Bloom level \
"{bloom_level}", grounded ONLY in the material below — never invent facts.

MATERIAL: {definition}

{misconception_block}

Return ONLY a JSON object:
For "mcq": {{"stem": "...", "key": "the correct option text", \
"distractors": [{{"text": "...", "misconception_index": N}}, ...]}} — every distractor MUST \
cite one of the numbered misconceptions above by index; never invent a new one.
For "numeric": {{"stem": "...", "key": "the numeric answer as a string", \
"computation": {{"formula": "...", "substitutions": {{"var": 1.0}}, "claimed_result": 1.0}}}}
For "short"/"derivation"/"application": {{"stem": "...", "key": "a model answer"}}
Or, if the material doesn't support a fair item at this level: {{"refused": true, "reason": "..."}}
"""


def _build_prompt(
    concept: Concept,
    definition_text: str,
    bloom_level: str,
    item_type: str,
    misconceptions: list[Misconception],
) -> str:
    misconception_block = ""
    if item_type == "mcq" and misconceptions:
        lines = [f"{i}: {m.description}" for i, m in enumerate(misconceptions)]
        misconception_block = "KNOWN MISCONCEPTIONS:\n" + "\n".join(lines)
    return _ITEM_PROMPT.format(
        item_type=item_type,
        name=concept.name,
        bloom_level=bloom_level,
        definition=definition_text,
        misconception_block=misconception_block,
    )


def _parse_item_response(
    response: str, item_type: str, misconceptions: list[Misconception], sink: DiagnosticSink
) -> GeneratedItem:
    try:
        raw = json.loads(response)
    except json.JSONDecodeError:
        return GeneratedItem(
            item_type=item_type, refused=True, refusal_reason="unparseable response"
        )

    if raw.get("refused"):
        return GeneratedItem(item_type=item_type, refused=True, refusal_reason=raw.get("reason"))

    stem = str(raw.get("stem", "")).strip()
    key = str(raw.get("key", "")).strip()
    if not stem or not key:
        return GeneratedItem(item_type=item_type, refused=True, refusal_reason="missing stem/key")

    distractors: list[Distractor] = []
    if item_type == "mcq":
        for d in raw.get("distractors", []):
            try:
                index = int(d["misconception_index"])
                misconception = misconceptions[index]
            except (KeyError, ValueError, TypeError, IndexError):
                sink.emit(
                    severity="warning",
                    code="distractor_missing_misconception",
                    message=f"dropped distractor with no valid misconception link: {d!r}",
                    pass_name=PASS_NAME,
                )
                continue
            text = str(d.get("text", "")).strip()
            if not text:
                continue
            distractors.append(Distractor(text=text, misconception_id=misconception.id))

    computation = None
    raw_computation = raw.get("computation") if item_type == "numeric" else None
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
            pass

    return GeneratedItem(
        item_type=item_type, stem=stem, key=key, distractors=distractors, computation=computation
    )


def generate_item(
    concept: Concept,
    definition_text: str,
    bloom_level: str,
    item_type: str,
    misconceptions: list[Misconception],
    sink: DiagnosticSink,
    *,
    backend: LLMBackend,
    model: str = ITEM_MODEL,
) -> GeneratedItem:
    from coursec.core import llm

    if not definition_text:
        return GeneratedItem(item_type=item_type, refused=True, refusal_reason="no source material")
    prompt = _build_prompt(concept, definition_text, bloom_level, item_type, misconceptions)
    response = llm.call(model, prompt, backend=backend)
    return _parse_item_response(response, item_type, misconceptions, sink)


def write_item(
    graph: Graph, concept: Concept, bloom_level: str, generated: GeneratedItem
) -> Item | None:
    if generated.refused or not generated.stem:
        return None
    if generated.item_type == "mcq" and not generated.distractors:
        return None  # every MCQ distractor must have a misconception link; none survived

    content = json.dumps(
        {
            "distractors": [
                {"text": d.text, "misconception_id": d.misconception_id}
                for d in generated.distractors
            ],
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
    content_hash = hashlib.sha256(
        f"{concept.id}:{bloom_level}:{generated.stem}".encode()
    ).hexdigest()
    item = graph.add(
        Item(
            created_by_pass=PASS_NAME,
            content_hash=content_hash,
            concept_id=concept.id,
            bloom_level=bloom_level,
            item_type=generated.item_type,
            stem=generated.stem,
            key=generated.key,
            content=content,
        )
    )

    graph.add(
        Edge(
            created_by_pass=PASS_NAME,
            content_hash=hashlib.sha256(f"{item.id}:{concept.id}:assesses".encode()).hexdigest(),
            source_id=item.id,
            target_id=concept.id,
            kind=EdgeKind.assesses,
        )
    )
    for distractor in generated.distractors:
        graph.add(
            Edge(
                created_by_pass=PASS_NAME,
                content_hash=hashlib.sha256(
                    f"{item.id}:{distractor.misconception_id}:assesses".encode()
                ).hexdigest(),
                source_id=item.id,
                target_id=distractor.misconception_id,
                kind=EdgeKind.assesses,
            )
        )
    return item
