"""The four render targets (PROMPTS.md D6). Each assembles Typst markup —
the "Document IR" for this stage — from the graph, then compiles it.

Every target reads the graph read-only; none of them regenerate anything an
earlier pass already produced (the cheat sheet uses `Concept`'s own source
span, never a `LessonBlock`; the answer key uses a stored `ExecResult`,
never a fresh computation).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import select

from coursec.core.graph import Graph
from coursec.core.models import (
    Block,
    Concept,
    ExecResult,
    Item,
    ItemStats,
    LessonBlock,
    SourceSpan,
)
from coursec.emit.bibliography import collect_bibliography
from coursec.emit.blueprint import Blueprint, SelectionResult, select_for_blueprint
from coursec.emit.contract import GENERATIVE_SLOTS, compute_all_contract_statuses
from coursec.emit.figures import crop_figure
from coursec.emit.mermaid import MermaidRenderer, render_via_mermaid_ink
from coursec.emit.typst_util import compile_typst, escape

_PAGE_SETUP = '#set page(paper: "a4", margin: 2cm)\n#set text(size: 10pt)\n\n'


class BlueprintInfeasible(Exception):
    """The question paper's blueprint cannot be satisfied. Carries the
    exact binding constraint — silent near-satisfaction is forbidden."""


def _definition_text(graph: Graph, concept: Concept) -> str:
    if concept.definition_span_id is None:
        return ""
    span = graph.session.get(SourceSpan, concept.definition_span_id)
    if span is None:
        return ""
    block = graph.session.get(Block, span.block_id)
    return block.text.strip() if block else ""


# --- Cheat sheet: pure template, zero LLM calls ------------------------------


def render_cheat_sheet(graph: Graph, concepts: list[Concept], *, work_dir: Path) -> bytes:
    """Definitions/formulas/key facts pulled straight from each Concept's
    own `SourceSpan` — never a `LessonBlock`, so this path can make no LLM
    call, directly or indirectly (see test_targets.py)."""
    lines = [_PAGE_SETUP, "= Cheat Sheet\n"]
    for concept in sorted(concepts, key=lambda c: c.name):
        text = _definition_text(graph, concept)
        if not text:
            continue
        lines.append(f"== {escape(concept.name)} ({escape(concept.concept_type.value)})")
        lines.append(escape(text))
        lines.append("")
    return compile_typst("\n".join(lines), work_dir=work_dir)


# --- Booklet: the full generated lesson --------------------------------------


def _slot_sentences(lesson_block: LessonBlock) -> list[str]:
    content = json.loads(lesson_block.content)
    return [s["text"] for s in content.get("sentences", [])]


def _slot_mermaid(lesson_block: LessonBlock) -> str | None:
    return json.loads(lesson_block.content).get("mermaid")


def _nearby_figure(graph: Graph, concept: Concept) -> Block | None:
    """A bound-caption figure on the same page as the concept's own
    definition — the same demo-grade "nearby visual" proxy `gap.py` uses,
    reused here since it's the only page-level index this project builds."""
    if concept.definition_span_id is None:
        return None
    span = graph.session.get(SourceSpan, concept.definition_span_id)
    if span is None:
        return None
    from coursec.core.models import BlockType

    figures = graph.session.exec(
        select(Block).where(Block.page == span.page, Block.block_type == BlockType.figure)
    ).all()
    return next((f for f in figures if f.bound_caption_id is not None), None)


def render_booklet(
    graph: Graph,
    concepts: list[Concept],
    *,
    work_dir: Path,
    mermaid_renderer: MermaidRenderer = render_via_mermaid_ink,
    pdf_path: Path | None = None,
) -> bytes:
    work_dir.mkdir(parents=True, exist_ok=True)
    statuses = compute_all_contract_statuses(graph, concepts)
    lines = [_PAGE_SETUP, "= Course Booklet\n"]

    for concept in sorted(concepts, key=lambda c: c.name):
        status = statuses[concept.id]
        badge = "complete" if status.complete else "partial"
        lines.append(f"== {escape(concept.name)} — _{badge}_")
        if not status.complete:
            unmet = ", ".join(status.unmet_slots)
            lines.append(f"_Unmet contract slots: {escape(unmet)}._\n")

        if pdf_path is not None:
            figure = _nearby_figure(graph, concept)
            if figure is not None:
                png_name = f"{figure.id}.png"
                (work_dir / png_name).write_bytes(crop_figure(pdf_path, figure))
                lines.append(f'#image("{png_name}")')

        blocks_by_slot = {
            b.slot: b
            for b in graph.session.exec(
                select(LessonBlock).where(
                    LessonBlock.concept_id == concept.id, LessonBlock.status != "quarantined"
                )
            )
        }
        for slot in GENERATIVE_SLOTS:
            block = blocks_by_slot.get(slot)
            if block is None:
                continue
            lines.append(f"=== {escape(slot.replace('_', ' ').title())}")
            if slot == "visual_or_analogy":
                mermaid_source = _slot_mermaid(block)
                if mermaid_source:
                    svg_name = f"{block.id}.svg"
                    (work_dir / svg_name).write_bytes(mermaid_renderer(mermaid_source))
                    lines.append(f'#image("{svg_name}")')
            else:
                for sentence in _slot_sentences(block):
                    lines.append(escape(sentence))
            lines.append("")

    bibliography = collect_bibliography(graph, concepts)
    if bibliography:
        lines.append("= Bibliography\n")
        for entry in bibliography:
            lines.append(f"- {escape(entry.domain)} ({escape(entry.tier)}): {escape(entry.url)}")

    return compile_typst("\n".join(lines), work_dir=work_dir)


# --- Question paper: greedy blueprint selection ------------------------------


def _accepted_items_with_stats(
    graph: Graph, concepts: list[Concept]
) -> list[tuple[Item, ItemStats | None]]:
    concept_ids = {c.id for c in concepts}
    pairs = []
    for item in graph.session.exec(select(Item).where(Item.status != "quarantined")):
        if item.concept_id not in concept_ids:
            continue
        stats = graph.session.exec(
            select(ItemStats).where(ItemStats.item_id == item.id)
        ).first()
        pairs.append((item, stats))
    return pairs


def select_question_paper_items(
    graph: Graph, concepts: list[Concept], blueprint: Blueprint
) -> SelectionResult:
    return select_for_blueprint(_accepted_items_with_stats(graph, concepts), blueprint)


def render_question_paper(
    graph: Graph, concepts: list[Concept], blueprint: Blueprint, *, work_dir: Path
) -> bytes:
    result = select_question_paper_items(graph, concepts, blueprint)
    if not result.feasible:
        raise BlueprintInfeasible(result.binding_constraint)

    lines = [_PAGE_SETUP, "= Question Paper", f"_Total marks: {result.achieved_marks}_\n"]
    for item in result.selected:
        lines.append(f"+ ({item.bloom_level}, {item.item_type}) {escape(item.stem)}")
        if item.item_type == "mcq":
            content = json.loads(item.content)
            options = [item.key, *[d["text"] for d in content.get("distractors", [])]]
            for option in options:
                lines.append(f"  - {escape(option)}")
    return compile_typst("\n".join(lines), work_dir=work_dir)


# --- Answer key: ExecResult, never regenerated -------------------------------


def render_answer_key(
    graph: Graph, concepts: list[Concept], blueprint: Blueprint, *, work_dir: Path
) -> bytes:
    """Mirrors `render_question_paper`'s selection exactly, so the two
    documents line up item-for-item. Numeric items show the sandboxed
    result already stored as an `ExecResult` — this function never calls
    `compute.check_worked_example` itself."""
    result = select_question_paper_items(graph, concepts, blueprint)
    if not result.feasible:
        raise BlueprintInfeasible(result.binding_constraint)

    lines = [_PAGE_SETUP, "= Answer Key\n"]
    for item in result.selected:
        lines.append(f"+ Key: {escape(item.key)}")
        if item.item_type == "numeric":
            exec_result = graph.session.exec(
                select(ExecResult).where(ExecResult.item_id == item.id)
            ).first()
            if exec_result is not None:
                shown = f"{exec_result.expression} -> {exec_result.result}"
                lines.append(f"  {escape(shown)}")
    return compile_typst("\n".join(lines), work_dir=work_dir)


@dataclass
class RenderedTargets:
    booklet: bytes
    cheat_sheet: bytes
    question_paper: bytes
    answer_key: bytes


def render_all_targets(
    graph: Graph,
    concepts: list[Concept],
    blueprint: Blueprint,
    *,
    work_dir: Path,
    mermaid_renderer: MermaidRenderer = render_via_mermaid_ink,
    pdf_path: Path | None = None,
) -> RenderedTargets:
    """**A build with any error-severity diagnostic produces no PDF at
    all** (CLAUDE.md, D6) — that gate is the caller's job (it needs the
    full-build `DiagnosticSink`, which this module doesn't have); this
    function assumes it has already been checked and always renders."""
    return RenderedTargets(
        booklet=render_booklet(
            graph,
            concepts,
            work_dir=work_dir / "booklet",
            mermaid_renderer=mermaid_renderer,
            pdf_path=pdf_path,
        ),
        cheat_sheet=render_cheat_sheet(graph, concepts, work_dir=work_dir / "cheat_sheet"),
        question_paper=render_question_paper(
            graph, concepts, blueprint, work_dir=work_dir / "question_paper"
        ),
        answer_key=render_answer_key(
            graph, concepts, blueprint, work_dir=work_dir / "answer_key"
        ),
    )
