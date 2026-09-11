"""The structure pass — CLAUDE.md §4: owns the `prerequisite_of` and
`part_of` Edge kinds; reads `Concept`.

**Prerequisite DAG.** Two fused signals, both required to create an edge
(PROMPTS.md D2):
  (a) mention — concept A's definition text mentions concept B's name.
  (b) an LLM pairwise judgement, run only over the candidate pairs (a)
      produces, unioned with same-section co-occurrence pairs — never all
      pairs (CLAUDE.md §8).
Cycles are broken by removing the lowest-confidence edge in each cycle found,
deterministic tie-break on (confidence, a content hash — see
`EdgeCandidate.content_hash`, standing in for PROMPTS.md's "edge_id": a
random uuid wouldn't make the tie-break reproducible run-to-run, which is
exactly what a *deterministic* tie-break is for). Every break is logged.

**part_of.** `understand` records which heading Block each Concept's
definition sits under as plain data (decision log #8); this pass is what
turns that into `Concept -part_of-> Block` edges.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from coursec.core import llm
from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Block, Concept, Edge, EdgeKind, SourceSpan

PASS_NAME = "structure"
CHEAP_MODEL = "claude-haiku-4-5-20251001"

LLMBackend = Callable[[str, str, dict[str, object]], str]


def _definition_text(graph: Graph, concept: Concept) -> str:
    if concept.definition_span_id is None:
        return ""
    span = graph.session.get(SourceSpan, concept.definition_span_id)
    if span is None:
        return ""
    block = graph.session.get(Block, span.block_id)
    return block.text.strip() if block else ""


def find_mention_pairs(graph: Graph, concepts: list[Concept]) -> set[tuple[str, str]]:
    """(mentioner_id, mentioned_id): mentioner's definition text mentions
    mentioned's name. Signal (a)."""
    definitions = {c.id: _definition_text(graph, c).lower() for c in concepts}
    pairs: set[tuple[str, str]] = set()
    for a in concepts:
        text = definitions[a.id]
        if not text:
            continue
        for b in concepts:
            if a.id == b.id or not b.name:
                continue
            if re.search(rf"\b{re.escape(b.name.lower())}\b", text):
                pairs.add((a.id, b.id))
    return pairs


def find_cooccurrence_pairs(section_by_concept: dict[str, str | None]) -> set[tuple[str, str]]:
    """Unordered pairs (as both directed tuples) of concepts whose
    definitions sit under the same heading."""
    by_section: dict[str, list[str]] = {}
    for concept_id, heading_id in section_by_concept.items():
        if heading_id is None:
            continue
        by_section.setdefault(heading_id, []).append(concept_id)

    pairs: set[tuple[str, str]] = set()
    for ids in by_section.values():
        for i in ids:
            for j in ids:
                if i != j:
                    pairs.add((i, j))
    return pairs


_JUDGE_PROMPT = """You are judging whether one concept is a prerequisite of another, for a \
course-compiler's prerequisite graph. A prerequisite relationship means a \
student must understand one concept before the other can make sense to them.

Concept A: {name_a}
Definition of A: {def_a}

Concept B: {name_b}
Definition of B: {def_b}

Return ONLY a JSON object (no prose, no markdown fence): \
{{"prerequisite": "A"|"B"|"neither", "confidence": <float 0-1>}}

"A" means A must be learned first (A is a prerequisite of B). "B" means B must \
be learned first. "neither" means there is no prerequisite relationship \
between them.
"""


@dataclass
class EdgeCandidate:
    source_id: str  # prerequisite
    target_id: str  # depends on source
    confidence: float
    content_hash: str


def judge_and_fuse(
    graph: Graph,
    concepts: list[Concept],
    mention_pairs: set[tuple[str, str]],
    candidate_pairs: set[tuple[str, str]],
    sink: DiagnosticSink,
    *,
    backend: LLMBackend,
    model: str = CHEAP_MODEL,
) -> list[EdgeCandidate]:
    """Signal (b), fused with signal (a). Called once per *unordered*
    candidate pair — never once per ordered pair, and never over all pairs."""
    concept_by_id = {c.id: c for c in concepts}
    definitions = {c.id: _definition_text(graph, c) for c in concepts}

    unordered_pairs = sorted({tuple(sorted(pair)) for pair in candidate_pairs})

    fused: list[EdgeCandidate] = []
    for a_id, b_id in unordered_pairs:
        a, b = concept_by_id[a_id], concept_by_id[b_id]
        prompt = _JUDGE_PROMPT.format(
            name_a=a.name,
            def_a=definitions[a_id],
            name_b=b.name,
            def_b=definitions[b_id],
        )
        response = llm.call(model, prompt, backend=backend)
        try:
            result = json.loads(response)
            verdict = result["prerequisite"]
            confidence = max(0.0, min(1.0, float(result["confidence"])))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            sink.emit(
                severity="warning",
                code="judgement_parse_error",
                message=f"could not parse pairwise judgement {response[:200]!r}: {exc}",
                pass_name=PASS_NAME,
            )
            continue

        if verdict == "A":
            source_id, target_id = a_id, b_id
        elif verdict == "B":
            source_id, target_id = b_id, a_id
        else:
            continue

        # Signal (a) must independently support the same direction: the
        # dependent concept's definition mentions the prerequisite's name.
        if (target_id, source_id) not in mention_pairs:
            continue

        content_hash = hashlib.sha256(
            f"{source_id}:{target_id}:{EdgeKind.prerequisite_of}".encode()
        ).hexdigest()
        fused.append(EdgeCandidate(source_id, target_id, confidence, content_hash))
    return fused


def _find_cycle(edges: list[EdgeCandidate]) -> list[EdgeCandidate] | None:
    """One cycle, as the list of edges forming it, or None if the graph is
    acyclic. Plain DFS with white/gray/black coloring — candidate counts at
    chapter scale make an external graph library unnecessary."""
    adjacency: dict[str, list[EdgeCandidate]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source_id, []).append(edge)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    path: list[EdgeCandidate] = []

    def visit(node: str) -> list[EdgeCandidate] | None:
        color[node] = GRAY
        for edge in adjacency.get(node, []):
            target = edge.target_id
            state = color.get(target, WHITE)
            if state == WHITE:
                path.append(edge)
                found = visit(target)
                if found is not None:
                    return found
                path.pop()
            elif state == GRAY:
                path.append(edge)
                start = next(i for i, e in enumerate(path) if e.source_id == target)
                cycle = list(path[start:])
                path.pop()
                return cycle
        color[node] = BLACK
        return None

    for node in sorted({e.source_id for e in edges} | {e.target_id for e in edges}):
        if color.get(node, WHITE) == WHITE:
            found = visit(node)
            if found is not None:
                return found
    return None


def break_cycles(
    candidates: list[EdgeCandidate], sink: DiagnosticSink
) -> list[EdgeCandidate]:
    """CLAUDE.md I4: break cycles by a deterministic documented policy, and
    log every break. Repeatedly finds one cycle and removes its
    lowest-confidence edge (tie-break: content_hash) until none remain."""
    surviving = list(candidates)
    while True:
        cycle = _find_cycle(surviving)
        if cycle is None:
            return surviving
        victim = min(cycle, key=lambda e: (e.confidence, e.content_hash))
        surviving = [e for e in surviving if e.content_hash != victim.content_hash]
        sink.emit(
            severity="info",
            code="prerequisite_cycle_broken",
            message=(
                f"removed {victim.source_id} -prerequisite_of-> {victim.target_id} "
                f"(confidence={victim.confidence:.2f}) to break a cycle"
            ),
            pass_name=PASS_NAME,
        )


def write_prerequisite_edges(graph: Graph, edges: list[EdgeCandidate]) -> list[Edge]:
    written = []
    for candidate in edges:
        written.append(
            graph.add(
                Edge(
                    created_by_pass=PASS_NAME,
                    content_hash=candidate.content_hash,
                    source_id=candidate.source_id,
                    target_id=candidate.target_id,
                    kind=EdgeKind.prerequisite_of,
                    confidence=candidate.confidence,
                )
            )
        )
    return written


def write_part_of_edges(
    graph: Graph, section_by_concept: dict[str, str | None]
) -> list[Edge]:
    written = []
    for concept_id, heading_block_id in section_by_concept.items():
        if heading_block_id is None:
            continue
        content_hash = hashlib.sha256(
            f"{concept_id}:{heading_block_id}:{EdgeKind.part_of}".encode()
        ).hexdigest()
        written.append(
            graph.add(
                Edge(
                    created_by_pass=PASS_NAME,
                    content_hash=content_hash,
                    source_id=concept_id,
                    target_id=heading_block_id,
                    kind=EdgeKind.part_of,
                )
            )
        )
    return written


def structure(
    graph: Graph,
    concepts: list[Concept],
    section_by_concept: dict[str, str | None],
    sink: DiagnosticSink,
    *,
    backend: LLMBackend,
    model: str = CHEAP_MODEL,
) -> tuple[list[Edge], list[Edge]]:
    """Run the full structure pass: prerequisite DAG + part_of edges.
    Returns (prerequisite_edges, part_of_edges)."""
    mention_pairs = find_mention_pairs(graph, concepts)
    cooccurrence_pairs = find_cooccurrence_pairs(section_by_concept)
    candidate_pairs = mention_pairs | cooccurrence_pairs

    fused = judge_and_fuse(
        graph, concepts, mention_pairs, candidate_pairs, sink, backend=backend, model=model
    )
    surviving = break_cycles(fused, sink)
    prerequisite_edges = write_prerequisite_edges(graph, surviving)
    part_of_edges = write_part_of_edges(graph, section_by_concept)
    return prerequisite_edges, part_of_edges
