import datetime as dt

from conftest import add_block_with_span

from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType, Edge, EdgeKind, WebEvidence
from coursec.passes.compose import CONTRACT_SLOTS
from coursec.passes.dossier import build_dossier
from coursec.passes.understand import _span_id_for_block

NOW = dt.datetime.now(dt.UTC)


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


def test_dossier_includes_source_span(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    dossier = build_dossier(graph, concept, all_slots=CONTRACT_SLOTS)
    assert concept.definition_span_id in dossier.source_span_ids
    assert f"[SPAN:{concept.definition_span_id}]" in dossier.prefix
    assert dossier.has_grounding


def test_dossier_with_no_source_and_no_evidence_has_no_grounding(graph: Graph) -> None:
    concept = graph.add(
        Concept(
            created_by_pass="understand",
            content_hash="h",
            name="ungrounded",
            concept_type=ConceptType.definition,
        )
    )
    dossier = build_dossier(graph, concept, all_slots=CONTRACT_SLOTS)
    assert dossier.has_grounding is False
    assert dossier.source_span_ids == []
    assert dossier.evidence_ids == []


def test_dossier_includes_admitted_evidence_not_rejected(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    admitted = graph.add(
        WebEvidence(
            created_by_pass="evidence", content_hash="h1", url="https://a.edu/x",
            domain="a.edu", retrieved_at=NOW,
            chunk_text="admitted chunk text", tier="tier1", admission="admitted", score=0.9,
        )
    )
    rejected = graph.add(
        WebEvidence(
            created_by_pass="evidence", content_hash="h2", url="https://b.example/y",
            domain="b.example", retrieved_at=NOW,
            chunk_text="rejected chunk text", tier="unknown", admission="rejected", score=0.1,
        )
    )
    for row in (admitted, rejected):
        graph.add(
            Edge(
                created_by_pass="evidence", content_hash=f"e:{row.id}",
                source_id=concept.id, target_id=row.id, kind=EdgeKind.evidenced_by,
            )
        )
    dossier = build_dossier(graph, concept, all_slots=CONTRACT_SLOTS)
    assert admitted.id in dossier.evidence_ids
    assert rejected.id not in dossier.evidence_ids
    assert "admitted chunk text" in dossier.prefix
    assert "rejected chunk text" not in dossier.prefix


def test_dossier_prefix_is_byte_stable_across_calls(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    first = build_dossier(graph, concept, all_slots=CONTRACT_SLOTS)
    second = build_dossier(graph, concept, all_slots=CONTRACT_SLOTS)
    assert first.prefix == second.prefix


def test_dossier_truncates_by_ascending_score_under_tight_budget(graph: Graph) -> None:
    concept = _concept(graph, "voltage", "Voltage is electric potential difference.")
    strong = graph.add(
        WebEvidence(
            created_by_pass="evidence", content_hash="s1", url="https://a.edu/x", domain="a.edu",
            retrieved_at=NOW, chunk_text=" ".join(["strongword"] * 50), tier="tier1",
            admission="admitted", score=0.9,
        )
    )
    weak = graph.add(
        WebEvidence(
            created_by_pass="evidence", content_hash="s2", url="https://b.edu/y", domain="b.edu",
            retrieved_at=NOW, chunk_text=" ".join(["weakword"] * 50), tier="tier1",
            admission="weak", score=0.1,
        )
    )
    for row in (strong, weak):
        graph.add(
            Edge(
                created_by_pass="evidence", content_hash=f"e:{row.id}",
                source_id=concept.id, target_id=row.id, kind=EdgeKind.evidenced_by,
            )
        )
    # Budget large enough for the header + source + one 50-word chunk, not both.
    dossier = build_dossier(graph, concept, all_slots=CONTRACT_SLOTS, token_budget=60)
    assert strong.id in dossier.evidence_ids
    assert weak.id not in dossier.evidence_ids
