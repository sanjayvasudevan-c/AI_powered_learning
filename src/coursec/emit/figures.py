"""Cropping source figures by their stored bbox (PROMPTS.md D6) — one of
only two legitimate ways an image enters an emitted document, the other
being generated diagram code (CLAUDE.md I7: licence-clean media only).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from coursec.core.models import Block

CROP_ZOOM = 2.0  # render at 2x for print-quality crops from a 72dpi PDF page


def crop_figure(pdf_path: Path, block: Block) -> bytes:
    """PNG bytes of `block`'s bbox, cropped from its original page."""
    doc = pymupdf.open(pdf_path)
    try:
        page = doc[block.page]
        rect = pymupdf.Rect(block.bbox)
        pixmap = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(CROP_ZOOM, CROP_ZOOM))
        return pixmap.tobytes("png")
    finally:
        doc.close()
