"""Shared test fixtures/helpers for D2+ pass tests."""

from __future__ import annotations

import hashlib

import pytest

import coursec.core.llm as llm_module
from coursec.core.graph import Graph
from coursec.core.models import Block, BlockType, SourceSpan


@pytest.fixture
def graph() -> Graph:
    return Graph()


@pytest.fixture(autouse=True)
def isolated_llm_cache(tmp_path, monkeypatch):
    """Every test gets its own `.cache/llm` directory.

    Without this, two tests (or two separate `make check` runs) whose
    concepts happen to produce byte-identical prompts — easy to do with
    short, reused fixture names like "a"/"b" — would silently share a cache
    entry: the second test's scripted backend would never be called, and it
    would see the first test's cached response instead. `llm.call`'s
    `cache_dir` default is resolved at call time from this module global
    specifically so monkeypatching it here works.
    """
    monkeypatch.setattr(llm_module, "DEFAULT_CACHE_DIR", tmp_path / ".cache" / "llm")


def add_block_with_span(
    graph: Graph,
    text: str,
    *,
    block_type: BlockType = BlockType.paragraph,
    page: int = 0,
) -> Block:
    """Ingest-shaped Block + SourceSpan pair, for tests that need real
    provenance to chain through (e.g. Concept.definition_span_id ->
    SourceSpan -> Block.text)."""
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    block = graph.add(
        Block(
            created_by_pass="ingest",
            content_hash=content_hash,
            file_id="test.pdf",
            page=page,
            bbox=[0.0, 0.0, 1.0, 1.0],
            text=text,
            block_type=block_type,
        )
    )
    graph.add(
        SourceSpan(
            created_by_pass="ingest",
            content_hash=content_hash,
            block_id=block.id,
            file_id="test.pdf",
            page=page,
            bbox=[0.0, 0.0, 1.0, 1.0],
            char_range=[0, len(text)],
            sha256=content_hash,
        )
    )
    return block
