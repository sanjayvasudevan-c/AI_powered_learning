"""Exercises `ui/quiz_app.py` through `streamlit.testing.v1.AppTest` — a
real click-through of the UI, not a mock of it. The pass logic it calls
into (`passes/learn.py`) has its own unit/property tests; this file is
about the plumbing: does the app read the right db, does clicking Submit
actually call `record_response`, does a wrong answer surface the root
cause.
"""

import hashlib
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from coursec.core.graph import Graph
from coursec.core.models import Concept, ConceptType, Edge, EdgeKind, Item

APP_PATH = str(Path(__file__).parent.parent / "src" / "coursec" / "ui" / "quiz_app.py")


def _seed_graph(db_path: Path) -> tuple[str, str]:
    """One prerequisite -> dependent pair, each with one accepted mcq item.
    Returns (prerequisite_concept_id, dependent_concept_id)."""
    with Graph(db_path) as graph:
        prereq = graph.add(
            Concept(
                created_by_pass="understand",
                content_hash="p",
                name="prerequisite concept",
                concept_type=ConceptType.definition,
            )
        )
        dependent = graph.add(
            Concept(
                created_by_pass="understand",
                content_hash="d",
                name="dependent concept",
                concept_type=ConceptType.definition,
            )
        )
        graph.add(
            Edge(
                created_by_pass="structure",
                content_hash="e",
                source_id=prereq.id,
                target_id=dependent.id,
                kind=EdgeKind.prerequisite_of,
            )
        )
        for concept, key in ((prereq, "prereq key"), (dependent, "dependent key")):
            graph.add(
                Item(
                    created_by_pass="assess",
                    content_hash=hashlib.sha256(f"{concept.id}:item".encode()).hexdigest(),
                    concept_id=concept.id,
                    bloom_level="remember",
                    item_type="mcq",
                    stem=f"Question about {concept.name}",
                    key=key,
                    content=json.dumps(
                        {"distractors": [{"text": "wrong", "misconception_id": "m1"}]}
                    ),
                    status="accepted",
                )
            )
        return prereq.id, dependent.id


def test_quiz_app_shows_first_question(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "coursec.db"
    _seed_graph(db_path)
    monkeypatch.setenv("COURSEC_DB_PATH", str(db_path))
    monkeypatch.setenv("COURSEC_STUDENT_ID", "alice")

    at = AppTest.from_file(APP_PATH)
    at.run()

    assert at.exception == []
    assert any("Question about" in md.value for md in at.subheader)
    assert len(at.radio) == 1


def test_submitting_a_correct_answer_shows_feedback_and_advances(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "coursec.db"
    _seed_graph(db_path)
    monkeypatch.setenv("COURSEC_DB_PATH", str(db_path))
    monkeypatch.setenv("COURSEC_STUDENT_ID", "alice")

    at = AppTest.from_file(APP_PATH)
    at.run()

    radio = at.radio[0]
    correct_option = next(o for o in radio.options if "key" in o)
    radio.set_value(correct_option)
    at.run()
    at.button[0].click()
    at.run()

    assert at.exception == []
    assert any("Correct." in info.value for info in at.info)


def test_submitting_a_wrong_answer_on_dependent_reports_root_cause(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "coursec.db"
    prereq_id, _dependent_id = _seed_graph(db_path)
    monkeypatch.setenv("COURSEC_DB_PATH", str(db_path))
    monkeypatch.setenv("COURSEC_STUDENT_ID", "alice")

    # Drive the prerequisite concept's mastery down first. BKT's repeated
    # incorrect-answer update converges to ~0.34 under DEFAULT_PARAMS —
    # still below dependent's never-observed prior (p_init=0.3) is false,
    # 0.34 > 0.3, so select_next_item (lowest mastery wins) asks the
    # *dependent* concept's item first — exactly the scenario this test
    # needs: a miss there should trace back to the now-weak prerequisite.
    from coursec.core.diagnostics import DiagnosticSink
    from coursec.passes import learn as learn_pass

    with Graph(db_path) as graph:
        sink = DiagnosticSink()
        for _ in range(5):
            learn_pass.record_response(
                graph, sink, student_id="alice", concept_id=prereq_id, correct=False
            )

    at = AppTest.from_file(APP_PATH)
    at.run()

    assert any("dependent concept" in sh.value for sh in at.subheader)

    radio = at.radio[0]
    wrong_option = next(o for o in radio.options if "key" not in o)
    radio.set_value(wrong_option)
    at.run()
    at.button[0].click()
    at.run()

    assert at.exception == []
    feedback = " ".join(info.value for info in at.info)
    assert "traces back to 'prerequisite concept'" in feedback
