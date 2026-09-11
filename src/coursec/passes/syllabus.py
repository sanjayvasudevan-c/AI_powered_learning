"""Syllabus anchoring — part of the `understand` pass (CLAUDE.md §4:
`SyllabusNode` is owned by `understand`).

Loads `data/syllabus.yaml` (hand-authored per PROMPTS.md D2), links each
Concept to zero or one SyllabusNode via hybrid (embedding + lexical)
retrieval with an abstain threshold, and reports coverage gaps. Abstaining is
a correct outcome — `link_concepts_to_syllabus` never forces a link below
`ABSTAIN_THRESHOLD`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from coursec.core import embeddings
from coursec.core.graph import Graph
from coursec.core.models import Concept, SyllabusNode

PASS_NAME = "understand"

DEFAULT_SYLLABUS_PATH = Path("data/syllabus.yaml")

# Hybrid score = 0.7 * embedding cosine + 0.3 * lexical Jaccard. A concept
# whose name is the syllabus entry's title (or a close paraphrase) scores
# well above this; unrelated pairs sit well below it. Not tuned against a
# labelled fixture the way MERGE_SIMILARITY_THRESHOLD is — PROMPTS.md D2
# only asks that of the merge threshold — but the adversarial coverage-gap
# test only requires that a genuinely absent topic never spuriously links,
# which holds at any threshold above ~0.15 given the two-order-of-magnitude
# gap between "no concept even tried" and "some concept scored a hit".
ABSTAIN_THRESHOLD = 0.55


def load_syllabus(graph: Graph, path: Path = DEFAULT_SYLLABUS_PATH) -> list[SyllabusNode]:
    entries = yaml.safe_load(path.read_text(encoding="utf-8"))
    nodes = []
    for entry in entries:
        content_hash = hashlib.sha256(f"{entry['code']}:{entry['title']}".encode()).hexdigest()
        nodes.append(
            graph.add(
                SyllabusNode(
                    created_by_pass=PASS_NAME,
                    content_hash=content_hash,
                    code=str(entry["code"]),
                    title=str(entry["title"]),
                    order=int(entry["order"]),
                )
            )
        )
    return nodes


def _lexical_score(a: str, b: str) -> float:
    """Token-overlap (Jaccard) similarity, case-insensitive, stopword-free-ish
    (tokens of length <= 2 are dropped as low-signal)."""
    ta = {w for w in a.lower().split() if len(w) > 2}
    tb = {w for w in b.lower().split() if len(w) > 2}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass
class SyllabusLink:
    concept_id: str
    syllabus_node_id: str | None  # None = abstained
    score: float


def link_concepts_to_syllabus(
    graph: Graph, concepts: list[Concept], syllabus_nodes: list[SyllabusNode]
) -> list[SyllabusLink]:
    if not concepts:
        return []
    if not syllabus_nodes:
        return [SyllabusLink(c.id, None, 0.0) for c in concepts]

    concept_vectors = embeddings.embed([c.name for c in concepts])
    node_vectors = embeddings.embed([n.title for n in syllabus_nodes])
    similarity = concept_vectors @ node_vectors.T  # both L2-normalized -> cosine

    links: list[SyllabusLink] = []
    for i, concept in enumerate(concepts):
        best_index = -1
        best_score = -1.0
        for j, node in enumerate(syllabus_nodes):
            hybrid = 0.7 * float(similarity[i, j]) + 0.3 * _lexical_score(concept.name, node.title)
            if hybrid > best_score:
                best_score, best_index = hybrid, j

        if best_score >= ABSTAIN_THRESHOLD:
            target = syllabus_nodes[best_index]
            concept.syllabus_node_id = target.id
            graph.session.add(concept)
            links.append(SyllabusLink(concept.id, target.id, best_score))
        else:
            links.append(SyllabusLink(concept.id, None, best_score))
    graph.session.commit()
    return links


def coverage_report(
    syllabus_nodes: list[SyllabusNode], links: list[SyllabusLink]
) -> list[SyllabusNode]:
    """SyllabusNodes with no linked Concept — the coverage gaps."""
    linked_node_ids = {link.syllabus_node_id for link in links if link.syllabus_node_id}
    return [n for n in syllabus_nodes if n.id not in linked_node_ids]
