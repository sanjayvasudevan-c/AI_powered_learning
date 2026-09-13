"""The project overview is a build output like any other target, so it gets
the same treatment: it must compile, it must be reproducible, and the PDF
committed next to its source must actually be the one that source produces.

That last check is the point of this file. A committed binary that silently
stops matching its source is worse than no committed binary at all — it looks
authoritative while being stale.
"""

import sys
from pathlib import Path

import pymupdf
import pytest

DOCS = Path(__file__).parent.parent / "docs"
sys.path.insert(0, str(DOCS))

from build_overview import OUTPUT, SOURCE, build  # noqa: E402


@pytest.fixture(scope="module")
def pdf_bytes() -> bytes:
    return build()


def test_overview_source_compiles(pdf_bytes: bytes) -> None:
    assert pdf_bytes.startswith(b"%PDF")


def test_two_builds_are_byte_identical() -> None:
    """The same property the cheat-sheet target is held to — a fixed compile
    timestamp is what makes a committed artifact verifiable at all."""
    assert build() == build()


def test_committed_pdf_matches_its_source(pdf_bytes: bytes) -> None:
    assert OUTPUT.exists(), "docs/overview.pdf is missing — run `make overview`"
    assert OUTPUT.read_bytes() == pdf_bytes, (
        "docs/overview.pdf is stale relative to docs/overview.typ — run `make overview`"
    )


def test_overview_covers_every_section(pdf_bytes: bytes) -> None:
    text = " ".join(page.get_text() for page in pymupdf.open(stream=pdf_bytes))
    for heading in (
        "In one paragraph",
        "The problem, stated precisely",
        "The thesis",
        "The seven invariants",
        "The intermediate representation",
        "The pipeline, pass by pass",
        "The stack",
        "How it is proven",
        "What ships",
        "What is deliberately not claimed",
        "Where it goes next",
    ):
        assert heading in text, f"overview lost its {heading!r} section"


def test_overview_names_all_ten_passes(pdf_bytes: bytes) -> None:
    text = " ".join(page.get_text() for page in pymupdf.open(stream=pdf_bytes))
    for pass_name in (
        "ingest", "understand", "structure", "gap", "evidence",
        "compose", "verify", "assess", "emit", "learn",
    ):
        assert pass_name in text, f"overview does not mention the {pass_name!r} pass"


def test_overview_keeps_its_honest_limits_section(pdf_bytes: bytes) -> None:
    """The limits table is the part most likely to be quietly dropped when a
    document is edited for brevity, and the part that makes the rest of it
    credible. Its claims are load-bearing, so they are asserted."""
    text = " ".join(page.get_text() for page in pymupdf.open(stream=pdf_bytes))
    assert "screen" in text and "not calibration" in text
    assert "no search provider is wired in by default" in text
    assert "Illustrative defaults" in text


def test_source_is_the_only_thing_that_needs_editing() -> None:
    assert SOURCE.exists()
    assert SOURCE.read_text(encoding="utf-8").lstrip().startswith("// CourseC")
