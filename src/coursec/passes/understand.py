"""The understand pass — CLAUDE.md §4: owns `Concept`, `Alias`,
`SyllabusNode`; reads `Block`.

Two sub-passes (PROMPTS.md D2):
  Pass A (`extract_candidates`) — one LLM call per section, proposing
    candidate concepts. Never one call per Block: a chapter has dozens of
    paragraphs but a handful of subsections, and CLAUDE.md §8 forbids
    calling an LLM in a loop over more than it needs.
  Pass B (`canonicalize`) — global, LLM-free: agglomerative clustering over
    local `bge-small` embeddings merges near-duplicate candidates into one
    Concept, with the rest recorded as `Alias` rows.

Section membership (which heading a Concept sits under) is returned as plain
data here, not written as a `part_of` Edge — that edge kind is owned by
`structure` (decision log #8); `Graph.add` would reject it from this module.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from sklearn.cluster import AgglomerativeClustering
from sqlmodel import select

from coursec.core import embeddings, llm
from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Alias, Block, BlockType, Concept, ConceptType, SourceSpan

PASS_NAME = "understand"
CHEAP_MODEL = "claude-haiku-4-5-20251001"

# Tuned against tests/fixtures/concept_merge_labels.json: the pair that must
# merge ("Ohm's law" / "V = IR relationship") embeds at cosine similarity
# 0.855; every pair that must not (including "variance"/"covariance" at
# 0.764 and "hypothesis"/"theory" at 0.773 — both deceptively close) sits
# below 0.78. 0.80 sits in the gap. See
# test_understand.py::test_merge_threshold_separates_labelled_pairs.
MERGE_SIMILARITY_THRESHOLD = 0.80

_CONTENT_TYPES = {BlockType.paragraph, BlockType.list, BlockType.equation}

LLMBackend = Callable[[str, str, dict[str, object]], str]


@dataclass
class Candidate:
    """One Pass-A proposal, before Pass B decides what it merges with."""

    name: str
    concept_type: ConceptType
    salience: float
    definition_span_id: str
    definition_text: str
    section_heading_id: str | None  # id of the heading Block this came from


@dataclass
class Section:
    heading: Block | None
    blocks: list[Block]


def chunk_by_section(blocks: list[Block]) -> list[Section]:
    """Group ingest's flat Block list into sections at heading boundaries —
    the "chunk" PROMPTS.md D2's Pass A operates on."""
    sections: list[Section] = []
    current = Section(heading=None, blocks=[])
    for block in blocks:
        if block.block_type == BlockType.heading:
            if current.blocks or current.heading is not None:
                sections.append(current)
            current = Section(heading=block, blocks=[])
        elif block.block_type in _CONTENT_TYPES:
            current.blocks.append(block)
    if current.blocks or current.heading is not None:
        sections.append(current)
    return sections


_EXTRACTION_PROMPT = """You are extracting candidate concepts from one section of a textbook \
chapter, for a course-compiler pipeline. A concept is a definition, formula, \
procedure, theorem, phenomenon, or worked example a student needs to learn — \
not narrative transitions or examples of prose style.

Section heading: {heading}

Numbered source passages:
{passages}

Return ONLY a JSON array (no prose, no markdown fence). Each element:
{{"name": <short name for the concept>, \
"type": one of "definition"|"formula"|"procedure"|"theorem"|"phenomenon"|"example", \
"salience": <float 0-1, how central this concept is to the section>, \
"source_index": <integer index of the passage above where it is defined>}}

Return [] if this section introduces no new concept. Never invent a concept \
that is not present in the passages above.
"""


def _heading_text(section: Section) -> str:
    return section.heading.text.strip() if section.heading else "(no heading)"


def _build_prompt(section: Section) -> tuple[str, dict[int, Block]]:
    passages_by_index = dict(enumerate(section.blocks))
    passages = "\n".join(f"[{i}] {b.text.strip()}" for i, b in passages_by_index.items())
    return _EXTRACTION_PROMPT.format(heading=_heading_text(section), passages=passages), (
        passages_by_index
    )


def _span_id_for_block(graph: Graph, block_id: str) -> str | None:
    span = graph.session.exec(select(SourceSpan).where(SourceSpan.block_id == block_id)).first()
    return span.id if span else None


def _parse_candidates(
    response: str,
    passages_by_index: dict[int, Block],
    section_heading_id: str | None,
    graph: Graph,
    sink: DiagnosticSink,
) -> list[Candidate]:
    try:
        raw = json.loads(response)
        if not isinstance(raw, list):
            raise ValueError("response is not a JSON array")
    except (json.JSONDecodeError, ValueError):
        sink.emit(
            severity="warning",
            code="candidate_parse_error",
            message=f"could not parse extraction response as a JSON array: {response[:200]!r}",
            pass_name=PASS_NAME,
        )
        return []

    candidates: list[Candidate] = []
    for item in raw:
        try:
            index = int(item["source_index"])
            block = passages_by_index[index]
            span_id = _span_id_for_block(graph, block.id)
            if span_id is None:
                raise KeyError("no SourceSpan for that source_index")
            candidates.append(
                Candidate(
                    name=str(item["name"]).strip(),
                    concept_type=ConceptType(item["type"]),
                    salience=max(0.0, min(1.0, float(item["salience"]))),
                    definition_span_id=span_id,
                    definition_text=block.text.strip(),
                    section_heading_id=section_heading_id,
                )
            )
        except (KeyError, ValueError, TypeError, IndexError) as exc:
            sink.emit(
                severity="warning",
                code="candidate_parse_error",
                message=f"dropping malformed candidate {item!r}: {exc}",
                pass_name=PASS_NAME,
            )
    return candidates


def extract_candidates(
    blocks: list[Block],
    graph: Graph,
    sink: DiagnosticSink,
    *,
    backend: LLMBackend,
    model: str = CHEAP_MODEL,
) -> list[Candidate]:
    """Pass A: one LLM call per section."""
    all_candidates: list[Candidate] = []
    for section in chunk_by_section(blocks):
        if not section.blocks:
            continue
        prompt, passages_by_index = _build_prompt(section)
        response = llm.call(model, prompt, backend=backend)
        heading_id = section.heading.id if section.heading else None
        all_candidates.extend(
            _parse_candidates(response, passages_by_index, heading_id, graph, sink)
        )
    return all_candidates


def canonicalize(
    candidates: list[Candidate], graph: Graph
) -> tuple[list[Concept], dict[str, str | None]]:
    """Pass B: agglomerative-cluster candidates by embedding similarity of
    `name: definition`. One Concept survives per cluster (highest salience);
    the rest become `Alias` rows. Returns the surviving Concepts plus a
    concept_id -> heading-Block-id map for `structure` to build `part_of`
    edges from."""
    if not candidates:
        return [], {}

    texts = [f"{c.name}: {c.definition_text}" for c in candidates]
    vectors = embeddings.embed(texts)

    if len(candidates) == 1:
        labels = [0]
    else:
        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=1.0 - MERGE_SIMILARITY_THRESHOLD,
            metric="cosine",
            linkage="average",
        )
        labels = clustering.fit_predict(vectors)

    clusters: dict[int, list[int]] = {}
    for i, label in enumerate(labels):
        clusters.setdefault(int(label), []).append(i)

    concepts: list[Concept] = []
    section_by_concept: dict[str, str | None] = {}
    for member_indices in clusters.values():
        member_indices.sort(key=lambda i: candidates[i].salience, reverse=True)
        canonical = candidates[member_indices[0]]
        content_hash = hashlib.sha256(
            f"{canonical.name}:{canonical.definition_text}".encode()
        ).hexdigest()
        concept = graph.add(
            Concept(
                created_by_pass=PASS_NAME,
                content_hash=content_hash,
                name=canonical.name,
                concept_type=canonical.concept_type,
                definition_span_id=canonical.definition_span_id,
                salience=canonical.salience,
            )
        )
        concepts.append(concept)
        section_by_concept[concept.id] = canonical.section_heading_id
        for i in member_indices[1:]:
            alias = candidates[i]
            graph.add(
                Alias(
                    created_by_pass=PASS_NAME,
                    content_hash=hashlib.sha256(alias.name.encode()).hexdigest(),
                    concept_id=concept.id,
                    alias_text=alias.name,
                )
            )
    return concepts, section_by_concept


def understand(
    blocks: list[Block],
    graph: Graph,
    sink: DiagnosticSink,
    *,
    backend: LLMBackend,
    model: str = CHEAP_MODEL,
) -> tuple[list[Concept], dict[str, str | None]]:
    candidates = extract_candidates(blocks, graph, sink, backend=backend, model=model)
    return canonicalize(candidates, graph)
