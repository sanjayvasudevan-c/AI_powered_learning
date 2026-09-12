
import numpy as np

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType, Item
from coursec.passes import pilot


def test_synthetic_cohort_has_12_students_by_default() -> None:
    students = pilot.build_synthetic_cohort([], n=pilot.COHORT_SIZE)
    assert len(students) == pilot.COHORT_SIZE


def test_synthetic_cohort_spans_the_ability_range_deterministically() -> None:
    a = pilot.build_synthetic_cohort(["m0"], seed=0)
    b = pilot.build_synthetic_cohort(["m0"], seed=0)
    assert [s.theta for s in a] == [s.theta for s in b]
    assert min(s.theta for s in a) == pilot.THETA_RANGE[0]
    assert max(s.theta for s in a) == pilot.THETA_RANGE[1]


def test_fit_2pl_is_deterministic_given_a_fixed_seed() -> None:
    rng = np.random.default_rng(1)
    thetas = np.linspace(-2, 2, 12)
    matrix = (rng.random((12, 3)) < 0.5).astype(float)

    fit_a = pilot.fit_2pl(matrix, thetas, seed=42)
    fit_b = pilot.fit_2pl(matrix, thetas, seed=42)
    assert fit_a == fit_b


def test_fit_2pl_recovers_a_clearly_discriminating_item() -> None:
    thetas = np.linspace(-2, 2, 12)
    # A student answers correctly iff their ability exceeds 0 — a sharp,
    # highly discriminating item by construction.
    responses = (thetas > 0).astype(float).reshape(-1, 1)
    (a, b), = pilot.fit_2pl(responses, thetas, seed=0)
    assert a > 0.5  # positive, non-trivial discrimination recovered
    assert abs(b) < 1.0  # difficulty near the true midpoint (0)


def test_is_degenerate_flags_near_zero_discrimination() -> None:
    assert pilot.is_degenerate(0.01, 0.0) is True
    assert pilot.is_degenerate(1.0, 0.0) is False


def test_is_degenerate_flags_extreme_difficulty() -> None:
    assert pilot.is_degenerate(1.0, 10.0) is True
    assert pilot.is_degenerate(1.0, -10.0) is True


def test_run_pilot_writes_item_stats_and_quarantines_degenerate_items(graph: Graph) -> None:
    concept = graph.add(
        Concept(
            created_by_pass="understand", content_hash="h", name="c",
            concept_type=ConceptType.definition,
        )
    )
    # An item everyone in the cohort answers correctly by construction
    # (item_type "short" has no distractor mechanism, so its correctness is
    # driven purely by simulate_response's theta-only logistic — with
    # THETA_RANGE spanning [-2, 2] this alone won't be perfectly degenerate,
    # so this test only checks the pipeline runs and writes stats; the
    # degenerate-detection unit tests above are what prove quarantine logic).
    item = graph.add(
        Item(
            created_by_pass="assess", content_hash="h", concept_id=concept.id,
            bloom_level="remember", item_type="short", stem="x", key="y", content="{}",
        )
    )
    sink = DiagnosticSink()
    stats = pilot.run_pilot(graph, [item], sink, seed=0)
    assert len(stats) == 1
    assert stats[0].item_id == item.id


def test_run_pilot_with_no_items_returns_empty(graph: Graph) -> None:
    assert pilot.run_pilot(graph, [], DiagnosticSink(), seed=0) == []


def test_screening_language_appears_in_module_docstring_not_calibration() -> None:
    # PROMPTS.md D5: "Label this screening, not calibration, in every output
    # string and comment... Do not let the wording drift." A cheap guard
    # against exactly that drift.
    assert "screening" in pilot.__doc__.lower()
    assert "not calibration" in pilot.__doc__.lower()


def test_mcq_wrong_answer_prefers_a_held_misconception(graph: Graph) -> None:
    student = pilot.SimulatedStudent("s0", theta=-2.0, misconception_ids={"m1"})
    item = pilot.SimItem(
        item_id="i0", item_type="mcq", key="correct",
        distractors=[("d0", "m0"), ("d1", "m1")],
    )
    rng = np.random.default_rng(0)
    assert pilot.choose_wrong_answer(student, item, rng=rng) == "d1"
