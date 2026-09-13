import hashlib
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType, Edge, EdgeKind, Item
from coursec.passes import learn


def _concept(graph: Graph, name: str) -> Concept:
    return graph.add(
        Concept(
            created_by_pass="understand",
            content_hash=hashlib.sha256(name.encode()).hexdigest(),
            name=name,
            concept_type=ConceptType.definition,
        )
    )


def _prerequisite_edge(graph: Graph, prereq: Concept, dependent: Concept) -> Edge:
    return graph.add(
        Edge(
            created_by_pass="structure",
            content_hash=hashlib.sha256(f"{prereq.id}:{dependent.id}".encode()).hexdigest(),
            source_id=prereq.id,
            target_id=dependent.id,
            kind=EdgeKind.prerequisite_of,
        )
    )


def _accepted_item(
    graph: Graph, concept: Concept, *, item_type: str = "short", key: str = "42", distractors=()
) -> Item:
    content = json.dumps({"distractors": list(distractors), "computation": None})
    item = graph.add(
        Item(
            created_by_pass="assess",
            content_hash=hashlib.sha256(f"{concept.id}:{key}".encode()).hexdigest(),
            concept_id=concept.id,
            bloom_level="remember",
            item_type=item_type,
            stem=f"What is {concept.name}?",
            key=key,
            content=content,
            status="accepted",
        )
    )
    return item


# -- bkt_update: pure-function unit + property tests -------------------------


def test_bkt_update_matches_hand_computed_values() -> None:
    params = learn.BKTParams(p_init=0.3, p_transit=0.3, p_slip=0.1, p_guess=0.25)

    # correct: (0.3*0.9) / (0.3*0.9 + 0.7*0.25) = 0.27 / 0.445 = 0.606742...
    # then transit: 0.606742 + (1 - 0.606742) * 0.3 = 0.72472
    correct_posterior = learn.bkt_update(0.3, True, params)
    assert correct_posterior == pytest.approx(0.724719, abs=1e-5)

    # incorrect: (0.3*0.1) / (0.3*0.1 + 0.7*0.75) = 0.03 / 0.555 = 0.054054...
    # then transit: 0.054054 + (1 - 0.054054) * 0.3 = 0.337838
    incorrect_posterior = learn.bkt_update(0.3, False, params)
    assert incorrect_posterior == pytest.approx(0.337838, abs=1e-5)


def test_bkt_update_rejects_out_of_range_prior() -> None:
    with pytest.raises(ValueError):
        learn.bkt_update(1.5, True)


def test_bkt_params_rejects_out_of_range_field() -> None:
    with pytest.raises(ValueError):
        learn.BKTParams(p_slip=1.5)


@given(prior=st.floats(min_value=0.0, max_value=1.0))
def test_correct_answer_never_leaves_mastery_lower_than_incorrect_would(prior: float) -> None:
    # DEFAULT_PARAMS has p_slip + p_guess < 1, which is exactly the
    # condition under which BKT's Bayes step can't rank "correct" below
    # "incorrect" from the same prior — see bkt_update's docstring.
    after_correct = learn.bkt_update(prior, True)
    after_incorrect = learn.bkt_update(prior, False)
    assert after_correct >= after_incorrect - 1e-12


@given(prior=st.floats(min_value=0.0, max_value=1.0))
def test_correct_answer_never_decreases_mastery(prior: float) -> None:
    assert learn.bkt_update(prior, True) >= prior - 1e-12


# -- latest_mastery / record_response -----------------------------------------


def test_latest_mastery_defaults_to_p_init_when_unobserved(graph: Graph) -> None:
    concept = _concept(graph, "voltage")
    assert learn.latest_mastery(graph, concept.id, "alice") == learn.DEFAULT_PARAMS.p_init


def test_record_response_appends_rather_than_overwrites(graph: Graph) -> None:
    concept = _concept(graph, "voltage")
    sink = DiagnosticSink()

    first = learn.record_response(
        graph, sink, student_id="alice", concept_id=concept.id, correct=True
    )
    second = learn.record_response(
        graph, sink, student_id="alice", concept_id=concept.id, correct=True
    )

    assert first.id != second.id
    assert learn.latest_mastery(graph, concept.id, "alice") == second.probability
    assert second.probability > first.probability  # a second correct answer keeps climbing
    assert sink.has_errors() is False


def test_record_response_is_scoped_per_student(graph: Graph) -> None:
    concept = _concept(graph, "voltage")
    sink = DiagnosticSink()

    learn.record_response(graph, sink, student_id="alice", concept_id=concept.id, correct=True)
    # bob has never answered — still at the prior, unaffected by alice's history.
    assert learn.latest_mastery(graph, concept.id, "bob") == learn.DEFAULT_PARAMS.p_init


# -- diagnose_root_cause -------------------------------------------------------


def test_root_cause_walks_to_the_deepest_weak_prerequisite(graph: Graph) -> None:
    a = _concept(graph, "a")  # deepest prerequisite
    b = _concept(graph, "b")  # middle
    c = _concept(graph, "c")  # the concept the student was actually quizzed on
    _prerequisite_edge(graph, a, b)
    _prerequisite_edge(graph, b, c)
    sink = DiagnosticSink()

    # Both a and b are weak (below WEAK_THRESHOLD); c is what was missed.
    for concept in (a, b):
        learn.record_response(
            graph, sink, student_id="alice", concept_id=concept.id, correct=False
        )

    root = learn.diagnose_root_cause(graph, c.id, "alice")

    assert root.concept_id == a.id
    assert root.depth == 2


def test_root_cause_stops_at_a_solid_prerequisite(graph: Graph) -> None:
    a = _concept(graph, "a")  # weak, but shielded by a solid b
    b = _concept(graph, "b")  # solid
    c = _concept(graph, "c")  # missed
    _prerequisite_edge(graph, a, b)
    _prerequisite_edge(graph, b, c)
    sink = DiagnosticSink()

    learn.record_response(graph, sink, student_id="alice", concept_id=a.id, correct=False)
    for _ in range(5):
        learn.record_response(graph, sink, student_id="alice", concept_id=b.id, correct=True)

    root = learn.diagnose_root_cause(graph, c.id, "alice")

    assert root.concept_id == c.id  # b is solid, so a (behind it) is never blamed
    assert root.depth == 0


def test_root_cause_defaults_to_the_concept_itself_with_no_prerequisites(graph: Graph) -> None:
    c = _concept(graph, "c")
    root = learn.diagnose_root_cause(graph, c.id, "alice")
    assert root == learn.RootCause(concept_id=c.id, depth=0)


def test_root_cause_picks_the_weaker_of_two_direct_prerequisites(graph: Graph) -> None:
    weaker = _concept(graph, "weaker")
    stronger_but_still_weak = _concept(graph, "stronger_but_still_weak")
    c = _concept(graph, "c")
    _prerequisite_edge(graph, weaker, c)
    _prerequisite_edge(graph, stronger_but_still_weak, c)
    sink = DiagnosticSink()

    learn.record_response(graph, sink, student_id="alice", concept_id=weaker.id, correct=False)
    learn.record_response(
        graph, sink, student_id="alice", concept_id=stronger_but_still_weak.id, correct=False
    )
    learn.record_response(
        graph, sink, student_id="alice", concept_id=stronger_but_still_weak.id, correct=True
    )

    root = learn.diagnose_root_cause(graph, c.id, "alice")
    assert root.concept_id == weaker.id


# -- mcq_options / select_next_item --------------------------------------------


def test_mcq_options_contains_key_and_all_distractors_exactly_once(graph: Graph) -> None:
    concept = _concept(graph, "voltage")
    item = _accepted_item(
        graph,
        concept,
        item_type="mcq",
        key="the key",
        distractors=[
            {"text": "wrong 1", "misconception_id": "m1"},
            {"text": "wrong 2", "misconception_id": "m2"},
        ],
    )
    options = learn.mcq_options(item)
    assert sorted(text for text, _ in options) == sorted(["the key", "wrong 1", "wrong 2"])
    assert sum(1 for _, is_correct in options if is_correct) == 1
    assert dict(options)["the key"] is True


def test_mcq_options_order_is_reproducible_for_the_same_item(graph: Graph) -> None:
    concept = _concept(graph, "voltage")
    item = _accepted_item(
        graph,
        concept,
        item_type="mcq",
        key="k",
        distractors=[{"text": "d1", "misconception_id": "m1"}],
    )
    assert learn.mcq_options(item) == learn.mcq_options(item)


def test_select_next_item_picks_the_weakest_concept(graph: Graph) -> None:
    weak = _concept(graph, "weak")
    strong = _concept(graph, "strong")
    weak_item = _accepted_item(graph, weak, key="w")
    _accepted_item(graph, strong, key="s")
    sink = DiagnosticSink()
    for _ in range(5):
        learn.record_response(graph, sink, student_id="alice", concept_id=strong.id, correct=True)

    chosen = learn.select_next_item(graph, "alice")
    assert chosen.id == weak_item.id


def test_select_next_item_excludes_already_asked_items(graph: Graph) -> None:
    concept = _concept(graph, "only")
    item = _accepted_item(graph, concept, key="k")
    chosen = learn.select_next_item(graph, "alice", asked_item_ids=frozenset({item.id}))
    assert chosen is None


def test_select_next_item_ignores_non_accepted_items(graph: Graph) -> None:
    concept = _concept(graph, "only")
    item = _accepted_item(graph, concept, key="k")
    item.status = "rejected"
    graph.session.add(item)
    graph.session.commit()

    assert learn.select_next_item(graph, "alice") is None


def test_select_next_item_returns_none_with_no_items(graph: Graph) -> None:
    assert learn.select_next_item(graph, "alice") is None
