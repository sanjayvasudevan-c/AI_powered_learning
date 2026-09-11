"""The origin linter — CLAUDE.md I2: "Numbers are computed, never
generated." Every numeric literal in emitted prose must trace to a
`SourceSpan`, a cited `WebEvidence` span, or an `ExecResult`. A literal with
no traceable origin is an **error** diagnostic — not a warning (CLAUDE.md
§2: "A violation is a build failure, never a warning, never a TODO").

Runs after verify's critic/repair loop, over whatever sentences survived —
scanning pre-repair prose would flag numbers the repair pass may have
already removed.
"""

from __future__ import annotations

import json
import re

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import LessonBlock
from coursec.passes.verify import _cited_text

PASS_NAME = "verify"

_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _numbers_in(text: str) -> set[str]:
    return {m for m in _NUMBER_RE.findall(text) if m}


def _exec_result_numbers(content: dict) -> set[str]:
    computation = content.get("computation")
    if not computation:
        return set()
    numbers = {str(v) for v in computation.get("substitutions", {}).values()}
    numbers.add(str(computation.get("claimed_result")))
    return numbers


def lint_lesson_block(graph: Graph, lesson_block: LessonBlock, sink: DiagnosticSink) -> int:
    """Returns the violation count."""
    content = json.loads(lesson_block.content)
    exec_numbers = _exec_result_numbers(content)

    violations = 0
    for index, sentence in enumerate(content.get("sentences", [])):
        cited_text = " ".join(
            _cited_text(graph, eid) for eid in sentence.get("evidence_ids", [])
        )
        allowed = _numbers_in(cited_text) | exec_numbers
        for literal in _numbers_in(sentence["text"]):
            if literal in allowed:
                continue
            violations += 1
            sink.emit(
                severity="error",
                code="numeric_origin_violation",
                message=(
                    f"sentence {index} of lesson block {lesson_block.id} has a numeric "
                    f"literal {literal!r} with no traceable origin"
                ),
                pass_name=PASS_NAME,
                node_id=lesson_block.id,
            )
    return violations
