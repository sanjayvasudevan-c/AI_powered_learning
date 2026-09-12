"""Typst compilation and markup-escaping shared by every target.

`typst.compile` takes a file path, not a source string, so every render
writes its assembled markup (plus any embedded images) into a scratch
directory first. A fixed `timestamp` is what makes "identical IR -> byte-
identical PDF" testable — Typst embeds the compile time in the PDF metadata
otherwise, which would make two compiles of the same input differ for a
reason that has nothing to do with the document.
"""

from __future__ import annotations

import re
from pathlib import Path

import typst

_ESCAPE_RE = re.compile(r'([\\*_#$@<>\[\]`])')


def escape(text: str) -> str:
    """Escape Typst markup metacharacters in plain text pulled from the
    graph (concept names, source prose) before splicing it into a .typ
    document."""
    return _ESCAPE_RE.sub(r"\\\1", text)


def compile_typst(source: str, *, work_dir: Path, timestamp: int = 0) -> bytes:
    """Write `source` to `work_dir/document.typ` and compile it, with
    `work_dir` as the Typst root so relative `image(...)` calls resolve
    against whatever assets the caller already wrote there."""
    work_dir.mkdir(parents=True, exist_ok=True)
    typ_path = work_dir / "document.typ"
    typ_path.write_text(source, encoding="utf-8")
    return typst.compile(str(typ_path), root=str(work_dir), timestamp=timestamp)
