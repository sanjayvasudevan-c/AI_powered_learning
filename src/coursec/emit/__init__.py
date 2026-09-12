"""The emit pass — CLAUDE.md §4: owns the rendered targets and the
certificate; reads everything else read-only.

Document IR (plain Typst-source strings assembled by each target's own
module) -> Typst -> PDF. Four targets: course booklet, cheat sheet (a pure
template over the graph — no LLM call anywhere in this path), question
paper (greedy blueprint selection), answer key (worked solutions pulled
from `ExecResult`, never regenerated).
"""
