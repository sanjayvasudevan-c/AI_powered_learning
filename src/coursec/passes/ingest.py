"""The ingest pass — CLAUDE.md §4: owns `Block`, `SourceSpan`; reads only
files.

PyMuPDF → classified `Block` rows, each with a `SourceSpan` recording its
exact provenance. Fully heuristic and deterministic — no LLM call anywhere in
this module (there is nothing in the spec for `ingest` that needs one, and
CLAUDE.md §8 says never to call one that isn't needed).

The classifier is a demo-grade typography/regex heuristic tuned against
`tests/fixtures/chapter.pdf` (an OpenStax page), not a general layout model.
That limitation is documented here rather than hidden, in the spirit of
CLAUDE.md §9's sandbox note.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

import pymupdf

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Block, BlockType, SourceSpan

PASS_NAME = "ingest"

_FIGURE_CAPTION_RE = re.compile(r"^\s*Figure\s+[\w.]+", re.IGNORECASE)
_TABLE_CAPTION_RE = re.compile(r"^\s*Table\s+[\w.]+", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*([•▪◦\-\*]|\d+[.)])\s+")
_MATH_CHARS = set("=×÷±∑∫√≈≠≤≥^")

# Tuned against the fixture corpus: body text sits at 9pt, sub-headings
# ("Solution", worked-example labels) at 10.8pt, section headings at 15.6pt.
_HEADING_FONT_SIZE = 10.0
# Running headers/footers (page numbers, chapter running head) live in this
# margin band, at a font size below the heading threshold.
_MARGIN_PT = 40.0

# A text block counts as "inside" a detected table if this much of its area
# overlaps the table's bbox — such blocks are dropped in favor of the single
# table Block, to avoid double-emitting cell text as paragraph/list blocks.
_TABLE_OVERLAP_THRESHOLD = 0.6

CAPTION_PROXIMITY_MAX_PT = 150.0


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rect_overlap_fraction(inner: pymupdf.Rect, outer: pymupdf.Rect) -> float:
    intersection = inner & outer
    if intersection.is_empty or inner.get_area() == 0:
        return 0.0
    return intersection.get_area() / inner.get_area()


def _classify_text(text: str, max_font_size: float, y0: float, page_height: float) -> BlockType:
    stripped = text.strip()
    if _FIGURE_CAPTION_RE.match(stripped) or _TABLE_CAPTION_RE.match(stripped):
        return BlockType.caption

    in_margin = y0 < _MARGIN_PT or y0 > page_height - _MARGIN_PT
    if in_margin and max_font_size < _HEADING_FONT_SIZE:
        # Boilerplate running header/footer, e.g. "Access for free at
        # openstax.org" or a running chapter title + page number. Lowest-
        # signal bucket rather than a heading.
        return BlockType.paragraph

    lines = [line for line in stripped.splitlines() if line.strip()]
    if lines:
        bullet_lines = sum(1 for line in lines if _BULLET_RE.match(line))
        if bullet_lines / len(lines) > 0.6:
            return BlockType.list

    word_count = len(stripped.split())
    if max_font_size > _HEADING_FONT_SIZE and word_count <= 12 and len(lines) <= 2:
        return BlockType.heading

    math_char_count = sum(stripped.count(c) for c in _MATH_CHARS)
    if math_char_count >= 1 and len(lines) <= 2 and len(stripped) < 80:
        return BlockType.equation

    return BlockType.paragraph


class _Draft:
    """One block's fields before it has an id — collected across the whole
    document in reading order so char_range offsets are assigned correctly,
    then turned into real (Block, SourceSpan) rows in one pass."""

    __slots__ = ("page", "bbox", "text", "block_type", "content_bytes")

    def __init__(
        self,
        page: int,
        bbox: list[float],
        text: str,
        block_type: BlockType,
        content_bytes: bytes,
    ) -> None:
        self.page = page
        self.bbox = bbox
        self.text = text
        self.block_type = block_type
        self.content_bytes = content_bytes


def _drafts_for_page(page: pymupdf.Page, page_no: int) -> list[_Draft]:
    page_height = page.rect.height
    tables = page.find_tables()
    table_rects = [pymupdf.Rect(t.bbox) for t in tables.tables]

    drafts: list[_Draft] = []
    for table in tables.tables:
        markdown = table.to_markdown()
        drafts.append(
            _Draft(page_no, list(table.bbox), markdown, BlockType.table, markdown.encode("utf-8"))
        )

    raw = page.get_text("dict")
    for b in raw["blocks"]:
        bbox = pymupdf.Rect(b["bbox"])
        if any(_rect_overlap_fraction(bbox, t) > _TABLE_OVERLAP_THRESHOLD for t in table_rects):
            continue  # already represented by a table Block

        if b["type"] == 1:  # image
            image_bytes = b.get("image")
            if not image_bytes:
                image_bytes = f"{page_no}:{list(bbox)}".encode()
            drafts.append(_Draft(page_no, list(bbox), "", BlockType.figure, image_bytes))
            continue

        lines_text = [
            "".join(span["text"] for span in line["spans"]) for line in b["lines"]
        ]
        text = "\n".join(lines_text)
        if not text.strip():
            continue
        sizes = [span["size"] for line in b["lines"] for span in line["spans"]]
        max_size = max(sizes) if sizes else 0.0
        block_type = _classify_text(text, max_size, bbox.y0, page_height)
        drafts.append(_Draft(page_no, list(bbox), text, block_type, text.encode("utf-8")))

    drafts.sort(key=lambda d: (d.bbox[1], d.bbox[0]))
    return drafts


def _bind_captions(blocks: list[Block], sink: DiagnosticSink) -> None:
    """Bind each figure Block to its caption. Numbering match first
    (assumes captions and figures both appear in document reading order —
    true whenever a chapter's figures are numbered sequentially, which is
    the case for the fixture corpus); geometric proximity second for
    whatever numbering match couldn't pair. Unbound figures are never
    silent — each gets a `caption_missing` diagnostic.
    """
    figures = [b for b in blocks if b.block_type == BlockType.figure]
    captions = [
        b
        for b in blocks
        if b.block_type == BlockType.caption and _FIGURE_CAPTION_RE.match(b.text.strip())
    ]

    bound_caption_ids: set[str] = set()
    if len(figures) == len(captions) and figures:
        for figure, caption in zip(figures, captions, strict=True):
            figure.bound_caption_id = caption.id
            bound_caption_ids.add(caption.id)

    unbound_figures = [f for f in figures if f.bound_caption_id is None]
    if unbound_figures:
        available_captions = [c for c in captions if c.id not in bound_caption_ids]
        for figure in unbound_figures:
            same_page = [c for c in available_captions if c.page == figure.page]
            if not same_page:
                continue
            nearest = min(same_page, key=lambda c: abs(c.bbox[1] - figure.bbox[3]))
            if abs(nearest.bbox[1] - figure.bbox[3]) <= CAPTION_PROXIMITY_MAX_PT:
                figure.bound_caption_id = nearest.id
                available_captions.remove(nearest)

    for figure in figures:
        if figure.bound_caption_id is None:
            sink.emit(
                severity="warning",
                code="caption_missing",
                message=f"figure Block on page {figure.page} has no bound caption",
                pass_name=PASS_NAME,
                node_id=figure.id,
            )


def ingest_pdf(pdf_path: Path, graph: Graph, sink: DiagnosticSink) -> list[Block]:
    """Run ingest over one PDF: write `Block` + `SourceSpan` rows to `graph`,
    bind figure captions, and return the created Blocks in document order."""
    doc = pymupdf.open(pdf_path)
    file_id = pdf_path.name

    drafts: list[_Draft] = []
    for page_no, page in enumerate(doc):
        drafts.extend(_drafts_for_page(page, page_no))
    doc.close()

    blocks: list[Block] = []
    offset = 0
    for draft in drafts:
        content_hash = _hash_bytes(draft.content_bytes)
        block = Block(
            created_by_pass=PASS_NAME,
            content_hash=content_hash,
            file_id=file_id,
            page=draft.page,
            bbox=draft.bbox,
            text=draft.text,
            block_type=draft.block_type,
        )
        graph.add(block)

        span_start = offset
        span_end = offset + len(draft.text)
        offset = span_end + 1
        span = SourceSpan(
            created_by_pass=PASS_NAME,
            content_hash=content_hash,
            block_id=block.id,
            file_id=file_id,
            page=draft.page,
            bbox=draft.bbox,
            char_range=[span_start, span_end],
            sha256=content_hash,
        )
        graph.add(span)
        blocks.append(block)

    _bind_captions(blocks, sink)
    graph.session.commit()

    return blocks


def block_type_histogram(blocks: list[Block]) -> Counter[str]:
    return Counter(b.block_type.value for b in blocks)
