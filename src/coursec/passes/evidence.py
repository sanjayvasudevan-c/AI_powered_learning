"""The evidence pass — CLAUDE.md §4: owns `WebEvidence` and the
`evidenced_by`/`contradicts` Edge kinds; reads `GapVector`.

`search` (query string -> candidate URLs) is a required, pluggable argument,
the same shape as `llm.call`'s `backend`: this project has no configured
search-API key, so there is no real production default — tests script their
own, and a live `coursec build` would need one wired in to actually retrieve
anything. Everything downstream of a URL — robots.txt compliance, per-domain
rate limiting, fetching, navigation stripping, heading-aware chunking,
scoring, and the admission policy — is fully real here and needs no key;
`Fetcher` works against any real HTTP server (or, in tests, an
`httpx.MockTransport`).
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import numpy as np
import yaml
from bs4 import BeautifulSoup

from coursec.core import embeddings
from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import (
    Block,
    Concept,
    ConceptType,
    Edge,
    EdgeKind,
    SourceSpan,
    WebEvidence,
)
from coursec.passes.gap import GapVector, RetrievalBudget

PASS_NAME = "evidence"

DEFAULT_SOURCES_PATH = Path("data/sources.yaml")

USER_AGENT = (
    "coursec-evidence/0.1 (+https://github.com/sanjayvasudevan-c/AI_powered_learning; "
    "educational course-compiler; respects robots.txt)"
)

ADMITTED = "admitted"
WEAK = "weak"
REJECTED = "rejected"

SearchBackend = Callable[[str], list[str]]


def no_search_backend(query: str) -> list[str]:
    """The production default: fails loudly, not silently.

    A real search step needs a provider (a search-API key, or a curated
    per-domain discovery mechanism this project doesn't have wired up).
    Guessing candidate URLs, or querying an endpoint whose robots.txt this
    same module would refuse to fetch, would both be worse than admitting
    the gap — so `coursec build` reaches the evidence stage, calls this, and
    stops here rather than fabricating retrieval results (CLAUDE.md I1/I2's
    "never fabricate" spirit, applied to evidence discovery rather than
    generation).
    """
    raise RuntimeError(
        "no search backend is configured for the evidence pass. Wire a real "
        "search provider into coursec.passes.evidence's `search` argument "
        "(e.g. a search-API client) and pass it explicitly — this project "
        "ships none by default."
    )


# ---------------------------------------------------------------------------
# Source tiers — data, not prompt text (PROMPTS.md D3)
# ---------------------------------------------------------------------------


@dataclass
class SourceTiers:
    tier1: list[str]
    tier2: list[str]
    blocklist: list[str]


def load_sources(path: Path = DEFAULT_SOURCES_PATH) -> SourceTiers:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SourceTiers(
        tier1=list(raw.get("tier1", [])),
        tier2=list(raw.get("tier2", [])),
        blocklist=list(raw.get("blocklist", [])),
    )


def _domain_matches(domain: str, pattern: str) -> bool:
    if pattern.startswith("*."):
        return domain == pattern[2:] or domain.endswith("." + pattern[2:])
    return domain == pattern


def domain_tier(domain: str, tiers: SourceTiers) -> str:
    """"tier1" | "tier2" | "blocklisted" | "unknown". A domain in no tier is
    never fetched, same as a blocklisted one, but is logged distinctly:
    there is no admission path for a domain PROMPTS.md D3's policy simply
    never defined (decision log)."""
    if any(_domain_matches(domain, p) for p in tiers.blocklist):
        return "blocklisted"
    if any(_domain_matches(domain, p) for p in tiers.tier1):
        return "tier1"
    if any(_domain_matches(domain, p) for p in tiers.tier2):
        return "tier2"
    return "unknown"


# ---------------------------------------------------------------------------
# Query synthesis — per gap dimension, not per concept
# ---------------------------------------------------------------------------


def synthesize_queries(
    concept: Concept, gap: GapVector, uncovered_ancestors: list[Concept]
) -> list[tuple[str, str]]:
    """(query, gap_dimension) pairs."""
    queries: list[tuple[str, str]] = []
    if gap.modality:
        queries.append((f"{concept.name} worked example", "modality"))
        queries.append((f"{concept.name} diagram", "modality"))
    if gap.depth:
        queries.append((f"{concept.name} derivation", "depth"))
    for ancestor in uncovered_ancestors:
        queries.append((f"{ancestor.name} explained", "prerequisite"))
    return queries


# ---------------------------------------------------------------------------
# Fetching — robots.txt, per-domain rate limiting, real httpx
# ---------------------------------------------------------------------------


@dataclass
class FetchResult:
    url: str
    status_code: int
    text: str
    fetched_at: datetime


class Fetcher:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        min_interval_seconds: float = 1.0,
        timeout: float = 10.0,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        self._min_interval = min_interval_seconds
        self._last_request_at: dict[str, float] = {}
        self._robots_cache: dict[str, RobotFileParser] = {}

    @staticmethod
    def _domain(url: str) -> str:
        return urlparse(url).netloc

    def _robots(self, url: str) -> RobotFileParser:
        domain = self._domain(url)
        if domain in self._robots_cache:
            return self._robots_cache[domain]
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        try:
            response = self._client.get(robots_url)
            parser.parse(response.text.splitlines() if response.status_code == 200 else [])
        except httpx.HTTPError:
            parser.parse([])
        self._robots_cache[domain] = parser
        return parser

    def can_fetch(self, url: str) -> bool:
        return self._robots(url).can_fetch(USER_AGENT, url)

    def _rate_limit(self, domain: str) -> None:
        last = self._last_request_at.get(domain)
        if last is not None:
            wait = self._min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at[domain] = time.monotonic()

    def fetch(self, url: str) -> FetchResult | None:
        if not self.can_fetch(url):
            return None
        self._rate_limit(self._domain(url))
        try:
            response = self._client.get(url)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        return FetchResult(
            url=url, status_code=response.status_code, text=response.text, fetched_at=_utcnow()
        )


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Navigation stripping + heading-aware chunking
# ---------------------------------------------------------------------------

_STRIP_TAGS = ("nav", "header", "footer", "script", "style", "aside", "form")
_CONTENT_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li")


def strip_navigation_and_chunk(html: str) -> list[tuple[str, str]]:
    """(heading, chunk_text) pairs. One chunk per heading section, the way
    `understand.chunk_by_section` treats the source PDF."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()

    root = soup.body or soup
    chunks: list[tuple[str, str]] = []
    current_heading = "(no heading)"
    current_parts: list[str] = []

    def flush() -> None:
        text = " ".join(current_parts).strip()
        if text:
            chunks.append((current_heading, text))

    for element in root.find_all(_CONTENT_TAGS):
        if element.name.startswith("h"):
            flush()
            current_heading = element.get_text(" ", strip=True)
            current_parts.clear()
        else:
            text = element.get_text(" ", strip=True)
            if text:
                current_parts.append(text)
    flush()
    return chunks


# ---------------------------------------------------------------------------
# Scoring: authority_prior * cos(chunk, concept) * pedagogical_fit *
#          recency^volatility - redundancy_penalty
# ---------------------------------------------------------------------------

AUTHORITY_PRIOR = {"tier1": 1.0, "tier2": 0.6}

# Foundational fact types don't go stale; worked procedures/examples do,
# mildly. recency^volatility with volatility=0 is 1.0 regardless of age —
# by design, a timeless fact is never penalized for its source's age.
VOLATILITY_BY_CONCEPT_TYPE = {
    ConceptType.definition: 0.0,
    ConceptType.theorem: 0.0,
    ConceptType.formula: 0.05,
    ConceptType.phenomenon: 0.1,
    ConceptType.procedure: 0.15,
    ConceptType.example: 0.15,
}

REDUNDANCY_SIMILARITY_THRESHOLD = 0.92


def _recency_term(
    fetched_at: datetime, last_modified: datetime | None, volatility: float
) -> float:
    if volatility <= 0.0 or last_modified is None:
        return 1.0
    age_years = max(0.0, (fetched_at - last_modified).days / 365.25)
    recency = max(0.05, 1.0 - 0.1 * age_years)
    return recency**volatility


def score_chunk(
    chunk_text: str,
    concept_embedding: np.ndarray,
    *,
    tier: str,
    query_dimension: str,
    fetched_at: datetime,
    concept_type: ConceptType,
    last_modified: datetime | None = None,
    already_admitted_embeddings: list[np.ndarray] = (),
) -> float:
    authority_prior = AUTHORITY_PRIOR.get(tier, 0.0)
    chunk_embedding = embeddings.embed([chunk_text])[0]
    cosine = float(chunk_embedding @ concept_embedding)

    # A chunk fetched specifically to fill a modality or depth gap is doing
    # exactly the job it was retrieved for; a prerequisite-explanation chunk
    # is one hop removed from the concept it will ultimately support.
    pedagogical_fit = 1.0 if query_dimension in ("modality", "depth") else 0.8

    volatility = VOLATILITY_BY_CONCEPT_TYPE.get(concept_type, 0.1)
    recency_term = _recency_term(fetched_at, last_modified, volatility)

    redundancy_penalty = 0.0
    if already_admitted_embeddings:
        max_sim = max(float(chunk_embedding @ e) for e in already_admitted_embeddings)
        if max_sim > REDUNDANCY_SIMILARITY_THRESHOLD:
            redundancy_penalty = max_sim - REDUNDANCY_SIMILARITY_THRESHOLD

    return authority_prior * cosine * pedagogical_fit * recency_term - redundancy_penalty


# ---------------------------------------------------------------------------
# Admission policy (autonomous, CLAUDE.md I6)
# ---------------------------------------------------------------------------


def decide_admission(
    *, already_in_source: bool, tier: str, corroborating_tier1_domains: set[str]
) -> tuple[str, str]:
    if already_in_source:
        return ADMITTED, "content already present in the source material"
    if len(corroborating_tier1_domains) >= 2:
        return (
            ADMITTED,
            f"corroborated by {len(corroborating_tier1_domains)} independent tier-1 domains",
        )
    if tier == "tier1":
        return WEAK, "single tier-1 source, not independently corroborated"
    return REJECTED, f"insufficient corroboration (tier={tier})"


_CANNOT_BE_SOLE_WEAK_EVIDENCE_FOR = frozenset({ConceptType.formula, ConceptType.definition})


def can_cite_as_sole_evidence(evidence_admission: str, concept_type: ConceptType) -> bool:
    """A `weak` chunk is usable for enrichment but never as the *sole*
    evidence for a formula, constant, or definition (PROMPTS.md D3)."""
    if evidence_admission != WEAK:
        return True
    return concept_type not in _CANNOT_BE_SOLE_WEAK_EVIDENCE_FOR


# ---------------------------------------------------------------------------
# Contradiction detection (CLAUDE.md I6: surfaced, never resolved)
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _extract_numbers(text: str) -> list[float]:
    numbers = []
    for raw in _NUMBER_RE.findall(text):
        try:
            numbers.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return numbers


def contradicts_source(
    source_text: str, chunk_text: str, *, relative_tolerance: float = 0.05
) -> bool:
    """Demo-grade: compares the leading numeric literal in each text. A real
    system would align matching quantities by context; this is enough to
    catch the adversarial "wrong constant" case without ever touching the
    source (I6 — the source stays primary, a contradiction is only ever
    surfaced alongside it)."""
    source_numbers = _extract_numbers(source_text)
    chunk_numbers = _extract_numbers(chunk_text)
    if not source_numbers or not chunk_numbers:
        return False
    source_value = source_numbers[0]
    if source_value == 0:
        return False
    return abs(source_value - chunk_numbers[0]) / abs(source_value) > relative_tolerance


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _definition_text(graph: Graph, concept: Concept) -> str:
    if concept.definition_span_id is None:
        return ""
    span = graph.session.get(SourceSpan, concept.definition_span_id)
    if span is None:
        return ""
    block = graph.session.get(Block, span.block_id)
    return block.text.strip() if block else ""


@dataclass
class _ConceptRetrievalState:
    tier1_domains: set[str] = field(default_factory=set)
    admitted_embeddings: list[np.ndarray] = field(default_factory=list)
    # Different gap dimensions can synthesize queries that a search backend
    # happens to answer with the same URL (e.g. "X worked example" and "X
    # diagram" both surfacing the same page). Refetching and re-admitting it
    # would double-count one source as if it were two.
    seen_urls: set[str] = field(default_factory=set)


def retrieve_evidence(
    graph: Graph,
    concepts: list[Concept],
    gap_vectors: dict[str, GapVector],
    budgets: dict[str, RetrievalBudget],
    sink: DiagnosticSink,
    *,
    search: SearchBackend,
    fetcher: Fetcher | None = None,
    sources_path: Path = DEFAULT_SOURCES_PATH,
) -> list[WebEvidence]:
    tiers = load_sources(sources_path)
    fetcher = fetcher or Fetcher()

    written: list[WebEvidence] = []
    for concept in concepts:
        budget = budgets.get(concept.id)
        if budget is None or budget.queries <= 0:
            continue

        gap = gap_vectors[concept.id]
        ancestors = graph.ancestors(concept.id, EdgeKind.prerequisite_of)
        uncovered_ancestors = [
            a for a in ancestors if isinstance(a, Concept) and a.syllabus_node_id is None
        ]
        queries = synthesize_queries(concept, gap, uncovered_ancestors)[: budget.queries]
        if not queries:
            continue

        source_text = _definition_text(graph, concept)
        concept_embedding = embeddings.embed([f"{concept.name}: {source_text}"])[0]
        state = _ConceptRetrievalState()

        for query, dimension in queries:
            for url in search(query):
                written.extend(
                    _process_url(
                        graph,
                        concept,
                        url,
                        dimension,
                        source_text,
                        concept_embedding,
                        tiers,
                        fetcher,
                        state,
                        sink,
                    )
                )
    return written


def _process_url(
    graph: Graph,
    concept: Concept,
    url: str,
    dimension: str,
    source_text: str,
    concept_embedding: np.ndarray,
    tiers: SourceTiers,
    fetcher: Fetcher,
    state: _ConceptRetrievalState,
    sink: DiagnosticSink,
) -> list[WebEvidence]:
    if url in state.seen_urls:
        return []
    state.seen_urls.add(url)

    domain = Fetcher._domain(url)
    tier = domain_tier(domain, tiers)

    if tier == "blocklisted":
        sink.emit(
            severity="info",
            code="evidence_blocklisted",
            message=f"skipped blocklisted domain {domain}",
            pass_name=PASS_NAME,
        )
        return []
    if tier == "unknown":
        sink.emit(
            severity="info",
            code="evidence_untiered_domain",
            message=f"skipped {domain}: not in tier1, tier2, or the blocklist",
            pass_name=PASS_NAME,
        )
        return []

    result = fetcher.fetch(url)
    if result is None:
        return []

    written: list[WebEvidence] = []
    for _heading, chunk_text in strip_navigation_and_chunk(result.text):
        written.extend(
            _admit_chunk(
                graph,
                concept,
                url,
                domain,
                tier,
                dimension,
                chunk_text,
                result.fetched_at,
                source_text,
                concept_embedding,
                state,
                sink,
            )
        )
    return written


def _admit_chunk(
    graph: Graph,
    concept: Concept,
    url: str,
    domain: str,
    tier: str,
    dimension: str,
    chunk_text: str,
    fetched_at: datetime,
    source_text: str,
    concept_embedding: np.ndarray,
    state: _ConceptRetrievalState,
    sink: DiagnosticSink,
) -> list[WebEvidence]:
    content_hash = hashlib.sha256(chunk_text.encode()).hexdigest()
    score = score_chunk(
        chunk_text,
        concept_embedding,
        tier=tier,
        query_dimension=dimension,
        fetched_at=fetched_at,
        concept_type=concept.concept_type,
        already_admitted_embeddings=state.admitted_embeddings,
    )

    if contradicts_source(source_text, chunk_text):
        evidence = graph.add(
            WebEvidence(
                created_by_pass=PASS_NAME,
                content_hash=content_hash,
                url=url,
                domain=domain,
                retrieved_at=fetched_at,
                chunk_text=chunk_text,
                tier=tier,
                admission=REJECTED,
                score=score,
            )
        )
        graph.add(
            Edge(
                created_by_pass=PASS_NAME,
                content_hash=hashlib.sha256(f"{evidence.id}:{concept.id}:contradicts".encode()).hexdigest(),
                source_id=evidence.id,
                target_id=concept.id,
                kind=EdgeKind.contradicts,
            )
        )
        sink.emit(
            severity="warning",
            code="evidence_contradiction",
            message=(
                f"{url} contradicts the source for {concept.name!r}; "
                "source kept as primary, divergence recorded"
            ),
            pass_name=PASS_NAME,
        )
        return [evidence]

    # Concept-level corroboration: how many *distinct tier-1 domains* have
    # already contributed non-contradicting evidence to this concept. A
    # simplification of "corroborated by >=2 independent tier-1 domains" —
    # real claim-level corroboration (do two sources agree on the *same*
    # fact, not just the same concept) needs entailment, out of scope here.
    prospective_domains = set(state.tier1_domains)
    if tier == "tier1":
        prospective_domains.add(domain)
    admission, reason = decide_admission(
        already_in_source=False, tier=tier, corroborating_tier1_domains=prospective_domains
    )

    evidence = graph.add(
        WebEvidence(
            created_by_pass=PASS_NAME,
            content_hash=content_hash,
            url=url,
            domain=domain,
            retrieved_at=fetched_at,
            chunk_text=chunk_text,
            tier=tier,
            admission=admission,
            score=score,
        )
    )
    sink.emit(
        severity="info",
        code=f"evidence_{admission}",
        message=f"{url} -> {admission} for {concept.name!r}: {reason}",
        pass_name=PASS_NAME,
    )

    if admission in (ADMITTED, WEAK):
        graph.add(
            Edge(
                created_by_pass=PASS_NAME,
                content_hash=hashlib.sha256(
                    f"{concept.id}:{evidence.id}:evidenced_by".encode()
                ).hexdigest(),
                source_id=concept.id,
                target_id=evidence.id,
                kind=EdgeKind.evidenced_by,
            )
        )
        if tier == "tier1":
            state.tier1_domains.add(domain)
        state.admitted_embeddings.append(embeddings.embed([chunk_text])[0])

    return [evidence]
