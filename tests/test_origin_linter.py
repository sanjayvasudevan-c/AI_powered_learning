
from conftest import add_block_with_span

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType
from coursec.passes import compose, origin_linter
from coursec.passes.understand import _span_id_for_block


def _concept_and_block(graph: Graph, definition: str, sentence_text: str, computation=None):
    block = add_block_with_span(graph, definition)
    concept = graph.add(
        Concept(
            created_by_pass="understand",
            content_hash="h",
            name="c",
            concept_type=ConceptType.definition,
            definition_span_id=_span_id_for_block(graph, block.id),
        )
    )
    span_ref = f"SPAN:{concept.definition_span_id}"
    generated = compose.GeneratedSlot(
        slot="definition",
        sentences=[compose.Sentence(text=sentence_text, evidence_ids=[span_ref])],
        computation=computation,
    )
    return concept, compose.write_lesson_block(graph, concept, generated)


def test_number_traceable_to_cited_span_is_not_a_violation(graph: Graph) -> None:
    _concept, block = _concept_and_block(
        graph,
        "Standard voltage is 5 volts in this circuit.",
        "The voltage here is 5 volts.",
    )
    sink = DiagnosticSink()
    violations = origin_linter.lint_lesson_block(graph, block, sink)
    assert violations == 0
    assert not sink.all()


def test_injected_unsourced_number_is_caught(graph: Graph) -> None:
    _concept, block = _concept_and_block(
        graph,
        "Standard voltage is 5 volts in this circuit.",
        "The voltage here is 5 volts, and in extreme cases reaches 999 volts.",
    )
    sink = DiagnosticSink()
    violations = origin_linter.lint_lesson_block(graph, block, sink)
    assert violations == 1
    errors = [d for d in sink.all() if d.code == "numeric_origin_violation"]
    assert len(errors) == 1
    assert errors[0].severity == "error"
    assert "999" in errors[0].message


def test_number_traceable_to_computation_is_not_a_violation(graph: Graph) -> None:
    computation = compose.Computation(
        formula="m*a", substitutions={"m": 2.0, "a": 3.0}, claimed_result=6.0
    )
    _concept, block = _concept_and_block(
        graph,
        "Force equals mass times acceleration.",
        "With mass 2.0 and acceleration 3.0, the force is 6.0.",
        computation=computation,
    )
    sink = DiagnosticSink()
    violations = origin_linter.lint_lesson_block(graph, block, sink)
    assert violations == 0
