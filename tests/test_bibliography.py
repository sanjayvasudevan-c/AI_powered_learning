import datetime as dt

from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType, Edge, EdgeKind, LessonBlock, WebEvidence
from coursec.emit.bibliography import collect_bibliography

NOW = dt.datetime.now(dt.UTC)


def _concept(graph: Graph) -> Concept:
    return graph.add(
        Concept(
            created_by_pass="understand", content_hash="h", name="c",
            concept_type=ConceptType.definition,
        )
    )


def _evidence(graph: Graph, url: str, domain: str) -> WebEvidence:
    return graph.add(
        WebEvidence(
            created_by_pass="evidence", content_hash=url, url=url, domain=domain,
            retrieved_at=NOW, chunk_text="x", tier="tier1", admission="admitted", score=0.9,
        )
    )


def test_collects_evidence_cited_directly_by_the_concept(graph: Graph) -> None:
    concept = _concept(graph)
    evidence = _evidence(graph, "https://a.edu/x", "a.edu")
    graph.add(
        Edge(
            created_by_pass="evidence", content_hash="e1", source_id=concept.id,
            target_id=evidence.id, kind=EdgeKind.evidenced_by,
        )
    )
    bib = collect_bibliography(graph, [concept])
    assert [e.url for e in bib] == ["https://a.edu/x"]


def test_collects_evidence_cited_by_a_lesson_block(graph: Graph) -> None:
    concept = _concept(graph)
    block = graph.add(
        LessonBlock(
            created_by_pass="compose", content_hash="h", concept_id=concept.id,
            slot="definition", content="{}",
        )
    )
    evidence = _evidence(graph, "https://b.edu/y", "b.edu")
    graph.add(
        Edge(
            created_by_pass="compose", content_hash="e2", source_id=block.id,
            target_id=evidence.id, kind=EdgeKind.evidenced_by,
        )
    )
    bib = collect_bibliography(graph, [concept])
    assert [e.url for e in bib] == ["https://b.edu/y"]


def test_ignores_evidenced_by_edges_targeting_a_source_span(graph: Graph) -> None:
    from coursec.core.models import Block, SourceSpan

    concept = _concept(graph)
    block = graph.add(
        Block(
            created_by_pass="ingest", content_hash="h", file_id="t.pdf", page=0,
            bbox=[0, 0, 1, 1], text="x", block_type="paragraph",
        )
    )
    span = graph.add(
        SourceSpan(
            created_by_pass="ingest", content_hash="h", block_id=block.id, file_id="t.pdf",
            page=0, bbox=[0, 0, 1, 1], char_range=[0, 1], sha256="h",
        )
    )
    lesson_block = graph.add(
        LessonBlock(
            created_by_pass="compose", content_hash="h2", concept_id=concept.id,
            slot="definition", content="{}",
        )
    )
    graph.add(
        Edge(
            created_by_pass="compose", content_hash="e3", source_id=lesson_block.id,
            target_id=span.id, kind=EdgeKind.evidenced_by,
        )
    )
    assert collect_bibliography(graph, [concept]) == []


def test_dedupes_and_sorts_by_domain_then_url(graph: Graph) -> None:
    concept = _concept(graph)
    e1 = _evidence(graph, "https://z.edu/late", "z.edu")
    e2 = _evidence(graph, "https://a.edu/early", "a.edu")
    for e in (e1, e2, e1):
        graph.add(
            Edge(
                created_by_pass="evidence", content_hash=f"dup-{e.id}", source_id=concept.id,
                target_id=e.id, kind=EdgeKind.evidenced_by,
            )
        )
    bib = collect_bibliography(graph, [concept])
    assert [e.url for e in bib] == ["https://a.edu/early", "https://z.edu/late"]
