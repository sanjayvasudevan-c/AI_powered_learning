"""Build `docs/overview.pdf` from `docs/overview.typ`.

Deliberately routed through `emit.typst_util.compile_typst` — the same
function the booklet and question-paper targets use — rather than calling
`typst.compile` directly. The overview's closing line claims it was set by
the same engine and the same code path as the course material it describes;
this is what makes that literally true rather than a nice turn of phrase.

`compile_typst`'s fixed timestamp is what makes the committed PDF
reproducible: Typst otherwise embeds the compile time in the metadata, and
two builds of an unchanged source would differ for a reason that has nothing
to do with the document. Both fonts the document asks for (Libertinus Serif,
DejaVu Sans Mono) ship inside Typst itself, so the output does not depend on
what happens to be installed on the machine.

    uv run python docs/build_overview.py        # or: make overview
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from coursec.emit.typst_util import compile_typst

DOCS = Path(__file__).parent
SOURCE = DOCS / "overview.typ"
OUTPUT = DOCS / "overview.pdf"


def build() -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        return compile_typst(SOURCE.read_text(encoding="utf-8"), work_dir=Path(tmp))


def main() -> int:
    pdf = build()
    previous = OUTPUT.read_bytes() if OUTPUT.exists() else None
    OUTPUT.write_bytes(pdf)
    state = "unchanged" if previous == pdf else ("updated" if previous else "created")
    print(f"{OUTPUT} — {len(pdf):,} bytes ({state})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
