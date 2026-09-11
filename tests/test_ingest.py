import json
from pathlib import Path

from coursec.core import llm
from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import BlockType
from coursec.passes.ingest import ingest_pdf

FIXTURE = Path(__file__).parent / "fixtures" / "chapter.pdf"
GOLDEN = Path(__file__).parent / "fixtures" / "chapter_blocks.golden.json"


def _run_ingest() -> tuple[list, DiagnosticSink]:
    sink = DiagnosticSink()
    graph = Graph()
    blocks = ingest_pdf(FIXTURE, graph, sink)
    return blocks, sink


def test_golden_block_snapshot_matches_committed_fixture() -> None:
    blocks, _ = _run_ingest()
    actual = [
        {
            "page": b.page,
            "block_type": b.block_type.value,
            "bbox": [round(x, 1) for x in b.bbox],
            "text_preview": b.text.strip()[:60],
            "content_hash": b.content_hash,
        }
        for b in blocks
    ]
    expected = json.loads(GOLDEN.read_text())
    assert actual == expected


def test_every_figure_block_has_a_bound_caption_or_a_diagnostic() -> None:
    blocks, sink = _run_ingest()
    missing_caption_node_ids = {
        d.node_id for d in sink.all() if d.code == "caption_missing"
    }
    for block in blocks:
        if block.block_type != BlockType.figure:
            continue
        assert block.bound_caption_id is not None or block.id in missing_caption_node_ids


def test_ingest_is_pure_parsing_and_issues_no_llm_calls() -> None:
    # The Ingest section of PROMPTS.md D1 describes a fully heuristic PyMuPDF
    # pass — no LLM call anywhere in it. Running it twice on identical input
    # must therefore leave the cache-miss counter at zero both times, which
    # also exercises the reset/assert pattern later LLM-calling passes reuse.
    llm.reset_cache_miss_count()
    _run_ingest()
    assert llm.cache_miss_count == 0
    _run_ingest()
    assert llm.cache_miss_count == 0


def test_ingest_writes_source_span_per_block() -> None:
    from sqlmodel import select

    from coursec.core.models import SourceSpan

    sink = DiagnosticSink()
    graph = Graph()
    blocks = ingest_pdf(FIXTURE, graph, sink)
    spans = list(graph.session.exec(select(SourceSpan)))
    assert len(spans) == len(blocks)
    span_block_ids = {s.block_id for s in spans}
    assert span_block_ids == {b.id for b in blocks}
