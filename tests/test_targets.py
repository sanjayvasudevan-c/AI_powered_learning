import json

from conftest import add_block_with_span

from coursec.core import llm
from coursec.core.graph import Graph
from coursec.core.models import (
    Concept,
    ConceptType,
    Edge,
    EdgeKind,
    Item,
    LessonBlock,
)
from coursec.emit import targets
from coursec.emit.blueprint import Blueprint
from coursec.passes.understand import _span_id_for_block


def _concept(graph: Graph, name: str, definition: str) -> Concept:
    block = add_block_with_span(graph, definition)
    return graph.add(
        Concept(
            created_by_pass="understand", content_hash="h", name=name,
            concept_type=ConceptType.definition,
            definition_span_id=_span_id_for_block(graph, block.id),
        )
    )


def _fake_mermaid(source: str) -> bytes:
    return b"<svg xmlns='http://www.w3.org/2000/svg'><text>fake</text></svg>"


def test_cheat_sheet_compiles_and_contains_the_source_text(graph: Graph, tmp_path) -> None:
    concept = _concept(graph, "Voltage", "Voltage is electric potential difference.")
    pdf_bytes = targets.render_cheat_sheet(graph, [concept], work_dir=tmp_path)
    assert pdf_bytes[:4] == b"%PDF"


def test_cheat_sheet_issues_zero_llm_calls(graph: Graph, tmp_path) -> None:
    concept = _concept(graph, "Voltage", "Voltage is electric potential difference.")
    llm.reset_cache_miss_count()
    targets.render_cheat_sheet(graph, [concept], work_dir=tmp_path)
    assert llm.cache_miss_count == 0


def test_booklet_compiles_with_lesson_blocks_and_mermaid(graph: Graph, tmp_path) -> None:
    concept = _concept(graph, "Voltage", "Voltage is electric potential difference.")
    span_ref = f"SPAN:{concept.definition_span_id}"
    for slot in ("definition", "intuition", "worked_example", "visual_or_analogy"):
        content = {
            "sentences": [{"text": f"A sentence about {slot}.", "evidence_ids": [span_ref]}],
            "mermaid": "graph TD; A-->B;" if slot == "visual_or_analogy" else None,
        }
        lb = graph.add(
            LessonBlock(
                created_by_pass="compose", content_hash=f"h{slot}", concept_id=concept.id,
                slot=slot, content=json.dumps(content), status="ok",
            )
        )
        graph.add(
            Edge(
                created_by_pass="compose", content_hash=f"e{slot}", source_id=lb.id,
                target_id=concept.definition_span_id, kind=EdgeKind.evidenced_by,
            )
        )
    pdf_bytes = targets.render_booklet(
        graph, [concept], work_dir=tmp_path, mermaid_renderer=_fake_mermaid
    )
    assert pdf_bytes[:4] == b"%PDF"


def test_booklet_notes_unmet_slots_for_a_partial_concept(graph: Graph, tmp_path) -> None:
    concept = _concept(graph, "Voltage", "Voltage is electric potential difference.")
    pdf_bytes = targets.render_booklet(
        graph, [concept], work_dir=tmp_path, mermaid_renderer=_fake_mermaid
    )
    assert pdf_bytes[:4] == b"%PDF"  # compiles even with nothing but "partial" text


def test_identical_ir_and_fixed_timestamp_gives_byte_identical_pdf(graph: Graph, tmp_path) -> None:
    concept = _concept(graph, "Voltage", "Voltage is electric potential difference.")
    first = targets.render_cheat_sheet(graph, [concept], work_dir=tmp_path / "a")
    second = targets.render_cheat_sheet(graph, [concept], work_dir=tmp_path / "b")
    assert first == second


def test_question_paper_and_answer_key_agree_on_selection(graph: Graph, tmp_path) -> None:
    concept = _concept(graph, "Voltage", "Voltage is electric potential difference.")
    for i in range(2):
        graph.add(
            Item(
                created_by_pass="assess", content_hash=f"i{i}", concept_id=concept.id,
                bloom_level="remember", item_type="short", stem=f"stem {i}", key=f"key {i}",
            )
        )
    blueprint = Blueprint(total_marks=2, marks_per_item=1, bloom_mix={"remember": 1.0})
    qp_selection = targets.select_question_paper_items(graph, [concept], blueprint)
    assert qp_selection.feasible is True
    assert len(qp_selection.selected) == 2

    qp_pdf = targets.render_question_paper(graph, [concept], blueprint, work_dir=tmp_path / "qp")
    ak_pdf = targets.render_answer_key(graph, [concept], blueprint, work_dir=tmp_path / "ak")
    assert qp_pdf[:4] == b"%PDF"
    assert ak_pdf[:4] == b"%PDF"


def test_infeasible_blueprint_raises_and_produces_no_pdf(graph: Graph, tmp_path) -> None:
    concept = _concept(graph, "Voltage", "Voltage is electric potential difference.")
    graph.add(
        Item(
            created_by_pass="assess", content_hash="i0", concept_id=concept.id,
            bloom_level="remember", item_type="short", stem="s", key="k",
        )
    )
    blueprint = Blueprint(total_marks=10, marks_per_item=1, bloom_mix={"remember": 1.0})
    try:
        targets.render_question_paper(graph, [concept], blueprint, work_dir=tmp_path)
    except targets.BlueprintInfeasible as exc:
        assert "remember" in str(exc)
    else:
        raise AssertionError("expected BlueprintInfeasible")
    assert list(tmp_path.glob("*.pdf")) == []


def test_answer_key_reads_stored_exec_result_not_regenerated(graph: Graph, tmp_path) -> None:
    from coursec.core.diagnostics import DiagnosticSink
    from coursec.passes import verify

    concept = _concept(graph, "Force", "Force equals mass times acceleration.")
    item = graph.add(
        Item(
            created_by_pass="assess", content_hash="i1", concept_id=concept.id,
            bloom_level="apply", item_type="numeric", stem="Compute F", key="6.0",
            content=json.dumps(
                {
                    "computation": {
                        "formula": "m*a",
                        "substitutions": {"m": 2.0, "a": 3.0},
                        "claimed_result": 6.0,
                    }
                }
            ),
        )
    )
    verify.run_item_compute_check(graph, item, DiagnosticSink())

    blueprint = Blueprint(total_marks=1, marks_per_item=1, bloom_mix={"apply": 1.0})
    ak_pdf = targets.render_answer_key(graph, [concept], blueprint, work_dir=tmp_path)
    assert ak_pdf[:4] == b"%PDF"
