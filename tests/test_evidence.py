from pathlib import Path

import httpx
from conftest import add_block_with_span
from sqlmodel import select

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType, Edge, EdgeKind
from coursec.passes import evidence
from coursec.passes.evidence import Fetcher, SourceTiers
from coursec.passes.gap import GapVector, RetrievalBudget
from coursec.passes.understand import _span_id_for_block

TIERS = SourceTiers(
    tier1=["openstax.org", "*.edu"],
    tier2=["hyperphysics.phy-astr.gsu.edu"],
    blocklist=["chegg.com"],
)


def test_domain_tier_exact_match() -> None:
    assert evidence.domain_tier("openstax.org", TIERS) == "tier1"


def test_domain_tier_wildcard_suffix() -> None:
    assert evidence.domain_tier("ocw.mit.edu", TIERS) == "tier1"
    assert evidence.domain_tier("mit.edu", TIERS) == "tier1"
    assert evidence.domain_tier("notedu.com", TIERS) == "unknown"


def test_domain_tier_blocklisted_wins() -> None:
    assert evidence.domain_tier("chegg.com", TIERS) == "blocklisted"


def test_domain_tier_unknown() -> None:
    assert evidence.domain_tier("random-blog.example", TIERS) == "unknown"


def test_load_sources_reads_real_data_file() -> None:
    tiers = evidence.load_sources(Path("data/sources.yaml"))
    assert "openstax.org" in tiers.tier1
    assert "chegg.com" in tiers.blocklist


def test_synthesize_queries_per_gap_dimension() -> None:
    concept = Concept(
        created_by_pass="understand", content_hash="h", name="Ohm's law",
        concept_type=ConceptType.formula,
    )
    gap_vector = GapVector(coverage=0, depth=1, modality=1, prerequisite=0)
    queries = evidence.synthesize_queries(concept, gap_vector, [])
    query_texts = [q for q, _dim in queries]
    assert "Ohm's law worked example" in query_texts
    assert "Ohm's law diagram" in query_texts
    assert "Ohm's law derivation" in query_texts


def test_synthesize_queries_includes_uncovered_ancestors() -> None:
    concept = Concept(
        created_by_pass="understand", content_hash="h", name="Ohm's law",
        concept_type=ConceptType.formula,
    )
    ancestor = Concept(
        created_by_pass="understand", content_hash="h", name="voltage",
        concept_type=ConceptType.definition,
    )
    gap_vector = GapVector(coverage=0, depth=0, modality=0, prerequisite=1)
    queries = evidence.synthesize_queries(concept, gap_vector, [ancestor])
    assert ("voltage explained", "prerequisite") in queries


def test_decide_admission_present_in_source() -> None:
    admission, _ = evidence.decide_admission(
        already_in_source=True, tier="tier2", corroborating_tier1_domains=set()
    )
    assert admission == evidence.ADMITTED


def test_decide_admission_two_tier1_domains() -> None:
    admission, _ = evidence.decide_admission(
        already_in_source=False, tier="tier1", corroborating_tier1_domains={"a.edu", "b.edu"}
    )
    assert admission == evidence.ADMITTED


def test_decide_admission_single_tier1_is_weak() -> None:
    admission, _ = evidence.decide_admission(
        already_in_source=False, tier="tier1", corroborating_tier1_domains={"a.edu"}
    )
    assert admission == evidence.WEAK


def test_decide_admission_tier2_alone_is_rejected() -> None:
    admission, _ = evidence.decide_admission(
        already_in_source=False, tier="tier2", corroborating_tier1_domains=set()
    )
    assert admission == evidence.REJECTED


def test_weak_chunk_cannot_be_sole_evidence_for_a_formula() -> None:
    assert evidence.can_cite_as_sole_evidence(evidence.WEAK, ConceptType.formula) is False
    assert evidence.can_cite_as_sole_evidence(evidence.WEAK, ConceptType.definition) is False


def test_weak_chunk_can_be_sole_evidence_for_enrichment_types() -> None:
    assert evidence.can_cite_as_sole_evidence(evidence.WEAK, ConceptType.example) is True


def test_admitted_chunk_can_be_sole_evidence_for_anything() -> None:
    assert evidence.can_cite_as_sole_evidence(evidence.ADMITTED, ConceptType.formula) is True


def test_contradicts_source_flags_a_materially_different_number() -> None:
    assert evidence.contradicts_source(
        "the speed of light is 300000 km/s", "the speed of light is 999999 km/s"
    )


def test_contradicts_source_tolerates_close_numbers() -> None:
    assert not evidence.contradicts_source(
        "the speed of light is 300000 km/s", "the speed of light is 299999 km/s"
    )


def test_contradicts_source_false_with_no_numbers() -> None:
    assert not evidence.contradicts_source("no numbers here", "also none here")


def test_strip_navigation_and_chunk_drops_nav_and_preserves_headings() -> None:
    html = """
    <html><body>
      <nav>Home | About | Contact</nav>
      <h1>Ohm's Law</h1>
      <p>Voltage equals current times resistance.</p>
      <h2>Derivation</h2>
      <p>Start from the definition of resistance.</p>
      <footer>Copyright 2026</footer>
    </body></html>
    """
    chunks = evidence.strip_navigation_and_chunk(html)
    headings = [h for h, _ in chunks]
    joined = " ".join(text for _, text in chunks)
    assert "Ohm's Law" in headings
    assert "Derivation" in headings
    assert "Home | About | Contact" not in joined
    assert "Copyright 2026" not in joined
    assert "Voltage equals current" in joined


# --- Fetcher: robots.txt + rate limiting, no real network -------------------


def _mock_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    if request.url.path.startswith("/private"):
        return httpx.Response(200, text="<html><body><p>should never be fetched</p></body></html>")
    return httpx.Response(
        200,
        text="<html><body><h1>Public</h1><p>public content</p></body></html>",
    )


def _fetcher() -> Fetcher:
    client = httpx.Client(transport=httpx.MockTransport(_mock_handler))
    return Fetcher(client=client, min_interval_seconds=0.0)


def test_fetcher_respects_robots_disallow() -> None:
    fetcher = _fetcher()
    assert fetcher.can_fetch("https://example.com/private/secret") is False
    assert fetcher.fetch("https://example.com/private/secret") is None


def test_fetcher_fetches_allowed_pages() -> None:
    fetcher = _fetcher()
    result = fetcher.fetch("https://example.com/page")
    assert result is not None
    assert "public content" in result.text


def test_fetcher_rate_limits_per_domain() -> None:
    fetcher = Fetcher(
        client=httpx.Client(transport=httpx.MockTransport(_mock_handler)),
        min_interval_seconds=0.2,
    )
    import time

    start = time.monotonic()
    fetcher.fetch("https://example.com/a")
    fetcher.fetch("https://example.com/b")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.2


# --- Full orchestration -------------------------------------------------


def _concept_with_definition(graph: Graph, name: str, definition: str, concept_type) -> Concept:
    block = add_block_with_span(graph, definition)
    return graph.add(
        Concept(
            created_by_pass="understand",
            content_hash="h",
            name=name,
            concept_type=concept_type,
            definition_span_id=_span_id_for_block(graph, block.id),
        )
    )


def test_blocklisted_domain_is_never_fetched(graph: Graph, tmp_path: Path) -> None:
    fetched_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched_urls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        return httpx.Response(200, text="<html><body><p>x 1 y</p></body></html>")

    fetcher = Fetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(
        "tier1: [openstax.org]\ntier2: []\nblocklist: [chegg.com]\n"
    )

    concept = _concept_with_definition(
        graph, "Ohm's law", "Ohm's law relates voltage current and resistance " * 4,
        ConceptType.formula,
    )
    gap_vectors = {concept.id: GapVector(coverage=0, depth=0, modality=1, prerequisite=0)}
    budgets = {concept.id: RetrievalBudget(concept.id, queries=5)}
    sink = DiagnosticSink()

    def search(query: str) -> list[str]:
        return ["https://chegg.com/answer", "https://openstax.org/page"]

    evidence.retrieve_evidence(
        graph, [concept], gap_vectors, budgets, sink,
        search=search, fetcher=fetcher, sources_path=sources_path,
    )

    assert not any("chegg.com" in u for u in fetched_urls)
    assert any(d.code == "evidence_blocklisted" for d in sink.all())


def test_adversarial_wrong_constant_produces_contradiction_not_overwrite(
    graph: Graph, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="")
        return httpx.Response(
            200,
            text=(
                "<html><body><h1>Speed of light</h1>"
                "<p>the speed of light is 999999999 km/s</p>"
                "</body></html>"
            ),
        )

    fetcher = Fetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text("tier1: []\ntier2: [low-authority.example]\nblocklist: []\n")

    concept = _concept_with_definition(
        graph, "speed of light",
        "the speed of light is 300000 km/s in a vacuum " * 4,
        ConceptType.formula,
    )
    definition_span_before = graph.session.get(
        type(concept), concept.id
    ).definition_span_id
    gap_vectors = {concept.id: GapVector(coverage=0, depth=0, modality=1, prerequisite=0)}
    budgets = {concept.id: RetrievalBudget(concept.id, queries=5)}
    sink = DiagnosticSink()

    written = evidence.retrieve_evidence(
        graph, [concept], gap_vectors, budgets, sink,
        search=lambda q: ["https://low-authority.example/wrong"],
        fetcher=fetcher, sources_path=sources_path,
    )

    assert any(w.admission == evidence.REJECTED for w in written)
    contradiction_edges = [
        e for e in graph.session.exec(select(Edge)) if e.kind == EdgeKind.contradicts
    ]
    assert len(contradiction_edges) == 1
    assert contradiction_edges[0].target_id == concept.id

    # The source itself must be untouched (I6: never overwrite).
    after = graph.session.get(type(concept), concept.id)
    assert after.definition_span_id == definition_span_before
    source_block_text = evidence._definition_text(graph, after)
    assert "300000" in source_block_text


def test_unknown_domain_is_never_fetched(graph: Graph, tmp_path: Path) -> None:
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        return httpx.Response(200, text="")

    fetcher = Fetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text("tier1: []\ntier2: []\nblocklist: []\n")

    concept = _concept_with_definition(
        graph, "a", "a definition " * 10, ConceptType.definition
    )
    gap_vectors = {concept.id: GapVector(coverage=0, depth=0, modality=1, prerequisite=0)}
    budgets = {concept.id: RetrievalBudget(concept.id, queries=5)}
    sink = DiagnosticSink()

    evidence.retrieve_evidence(
        graph, [concept], gap_vectors, budgets, sink,
        search=lambda q: ["https://random.example/page"],
        fetcher=fetcher, sources_path=sources_path,
    )
    assert fetched == []  # never even reached robots.txt


def test_no_budget_means_no_retrieval(graph: Graph) -> None:
    concept = _concept_with_definition(
        graph, "a", "a definition " * 10, ConceptType.definition
    )
    calls = []

    def search(query: str) -> list[str]:
        calls.append(query)
        return []

    gap_vectors = {concept.id: GapVector(coverage=0, depth=0, modality=1, prerequisite=0)}
    budgets = {concept.id: RetrievalBudget(concept.id, queries=0)}
    sink = DiagnosticSink()
    written = evidence.retrieve_evidence(
        graph, [concept], gap_vectors, budgets, sink, search=search
    )
    assert written == []
    assert calls == []
