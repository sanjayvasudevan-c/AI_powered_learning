"""Web API tests.

Every endpoint is exercised against a real on-disk graph built the same
way the passes build one — no mocked ORM, no fixture JSON — plus the
no-database path, which is a first-class answer here rather than a 500.
"""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import (
    Block,
    BlockType,
    Concept,
    ConceptType,
    Edge,
    EdgeKind,
    ExecResult,
    Item,
    ItemStats,
    LessonBlock,
    Misconception,
    SourceSpan,
    SyllabusNode,
    Verdict,
)
from coursec.passes import learn as learn_pass
from coursec.web import api as web_api


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """A client pointed at a database path that does not exist yet — each
    test decides whether to populate it, so the empty case is the default
    rather than an afterthought."""
    monkeypatch.setattr(web_api.settings, "db_path", tmp_path / "coursec.db")
    return TestClient(web_api.app)


def _seed(db_path: Path) -> dict[str, str]:
    """One prerequisite pair, both with real spans, one with a full slot
    set and an accepted mcq item carrying a real misconception."""
    ids: dict[str, str] = {}
    with Graph(db_path) as graph:
        node = graph.add(
            SyllabusNode(
                created_by_pass="understand", content_hash="s", code="1.1",
                title="Brightness and distance", order=1,
            )
        )

        def concept(name: str, text: str, *, linked: bool) -> Concept:
            h = hashlib.sha256(name.encode()).hexdigest()
            block = graph.add(
                Block(
                    created_by_pass="ingest", content_hash=h, file_id="chapter.pdf",
                    page=14, bbox=[0.0, 0.0, 1.0, 1.0], text=text,
                    block_type=BlockType.paragraph,
                )
            )
            span = graph.add(
                SourceSpan(
                    created_by_pass="ingest", content_hash=h, block_id=block.id,
                    file_id="chapter.pdf", page=14, bbox=[0.0, 0.0, 1.0, 1.0],
                    char_range=[0, len(text)], sha256=h,
                )
            )
            return graph.add(
                Concept(
                    created_by_pass="understand", content_hash=h, name=name,
                    concept_type=ConceptType.formula, definition_span_id=span.id,
                    salience=0.9, syllabus_node_id=node.id if linked else None,
                )
            )

        prereq = concept("Luminosity", "Luminosity is total radiated power.", linked=True)
        dependent = concept(
            "Inverse-square law", "Brightness falls off as the square of distance.", linked=False
        )
        graph.add(
            Edge(
                created_by_pass="structure", content_hash="e",
                source_id=prereq.id, target_id=dependent.id,
                kind=EdgeKind.prerequisite_of, confidence=0.9,
            )
        )

        span_id = graph.session.get(Concept, dependent.id).definition_span_id
        for slot in ("definition", "intuition", "worked_example", "visual_or_analogy"):
            content = {
                "sentences": [
                    {"text": f"A grounded {slot} sentence.", "evidence_ids": [f"SPAN:{span_id}"]}
                ],
                "mermaid": None,
                "computation": (
                    {"formula": "m*a", "substitutions": {"m": 2.0, "a": 3.0},
                     "claimed_result": 6.0}
                    if slot == "worked_example" else None
                ),
            }
            block = graph.add(
                LessonBlock(
                    created_by_pass="compose", content_hash=slot, concept_id=dependent.id,
                    slot=slot, content=json.dumps(content), status="ok",
                )
            )
            graph.add(
                Verdict(
                    created_by_pass="verify", content_hash=f"v{slot}",
                    lesson_block_id=block.id, sentence_index=0,
                    classification="entailed", sentence_text="A grounded sentence.",
                )
            )
            if slot == "worked_example":
                graph.add(
                    ExecResult(
                        created_by_pass="verify", content_hash="x",
                        expression="m*a claims 6.0", result="{}", success=True,
                        lesson_block_id=block.id,
                    )
                )

        misconception = graph.add(
            Misconception(
                created_by_pass="assess", content_hash="m", concept_id=dependent.id,
                description="Treats brightness as linear in distance rather than inverse-square.",
            )
        )
        for i, key in enumerate(("one quarter", "one ninth")):
            item = graph.add(
                Item(
                    created_by_pass="assess", content_hash=f"i{i}", concept_id=dependent.id,
                    bloom_level="apply", item_type="mcq",
                    stem=f"Question {i} about brightness?", key=key,
                    content=json.dumps({
                        "distractors": [
                            {"text": "twice as faint", "misconception_id": misconception.id}
                        ],
                        "computation": None,
                    }),
                    status="accepted",
                )
            )
            graph.add(
                ItemStats(
                    created_by_pass="assess", content_hash=f"st{i}", item_id=item.id,
                    discrimination=1.2, difficulty=0.3, quarantined=False,
                )
            )
            ids[f"item{i}"] = item.id

        ids["prereq"] = prereq.id
        ids["dependent"] = dependent.id
    return ids


# ── the no-database path ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "/api/summary", "/api/graph", "/api/certificate",
        "/api/quiz/next", "/api/quiz/mastery", "/api/concepts/whatever",
    ],
)
def test_every_read_endpoint_reports_unavailable_rather_than_failing(client, url) -> None:
    response = client.get(url)
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert "coursec build" in body["reason"]


def test_landing_page_serves_without_any_database(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "A course" in response.text
    assert "compiler" in response.text


def test_database_appearing_later_is_picked_up_without_restart(client, tmp_path) -> None:
    assert client.get("/api/summary").json()["available"] is False
    _seed(tmp_path / "coursec.db")
    assert client.get("/api/summary").json()["available"] is True


# ── read endpoints over a real graph ─────────────────────────────────


def test_summary_counts_the_real_graph(client, tmp_path) -> None:
    _seed(tmp_path / "coursec.db")
    body = client.get("/api/summary").json()
    assert body["concepts"] == 2
    assert body["prerequisite_edges"] == 1
    assert body["items_accepted"] == 2
    assert body["coverage_pct"] == 100.0  # one syllabus node, one concept linked
    assert body["source_file"] == "chapter.pdf"


def test_graph_endpoint_assigns_depth_along_prerequisite_edges(client, tmp_path) -> None:
    ids = _seed(tmp_path / "coursec.db")
    body = client.get("/api/graph").json()
    depth = {c["id"]: c["depth"] for c in body["concepts"]}
    assert depth[ids["prereq"]] == 0
    assert depth[ids["dependent"]] == 1
    assert body["edges"][0]["source"] == ids["prereq"]


def test_graph_endpoint_flags_an_unmet_contract(client, tmp_path) -> None:
    ids = _seed(tmp_path / "coursec.db")
    body = client.get("/api/graph").json()
    by_id = {c["id"]: c for c in body["concepts"]}
    assert by_id[ids["dependent"]]["contract_complete"] is True  # 4 slots + 2 items
    assert by_id[ids["prereq"]]["contract_complete"] is False  # no slots, no items
    assert by_id[ids["prereq"]]["unmet_slots"]


def test_concept_detail_carries_slots_citations_and_provenance(client, tmp_path) -> None:
    ids = _seed(tmp_path / "coursec.db")
    body = client.get(f"/api/concepts/{ids['dependent']}").json()

    assert body["name"] == "Inverse-square law"
    assert body["provenance"]["page"] == 14
    assert body["prerequisites"][0]["name"] == "Luminosity"

    slots = {s["slot"]: s for s in body["slots"]}
    assert set(slots) == {"definition", "intuition", "worked_example", "visual_or_analogy"}
    # every sentence resolves its citation back to a real span, not a bare id
    citation = slots["definition"]["sentences"][0]["citations"][0]
    assert citation["kind"] == "span"
    assert citation["label"] == "p.14"
    assert slots["worked_example"]["executed"][0]["agrees"] is True


def test_concept_detail_404s_on_an_unknown_id(client, tmp_path) -> None:
    _seed(tmp_path / "coursec.db")
    assert client.get("/api/concepts/nope").status_code == 404


def test_certificate_ledger_is_derived_not_replayed(client, tmp_path) -> None:
    _seed(tmp_path / "coursec.db")
    body = client.get("/api/certificate").json()

    assert [row["code"] for row in body["ledger"]] == ["I1", "I2", "I3", "I4", "I5", "I6", "I7"]
    assert all(row["held"] for row in body["ledger"])
    assert body["emission_withheld"] is False
    assert "not persisted" in body["derived_from"]
    assert body["measured"]["items_accepted"] == 2
    assert body["pilot"]["label"] == "screening, not calibration"


def test_certificate_withholds_emission_on_a_failed_computation(client, tmp_path) -> None:
    """The I2 row is the one that actually reads rows rather than asserting
    a property, so it is the one worth proving flips."""
    _seed(tmp_path / "coursec.db")
    with Graph(tmp_path / "coursec.db") as graph:
        graph.add(
            ExecResult(
                created_by_pass="verify", content_hash="bad",
                expression="m*a claims 5.0", result="{}", success=False,
            )
        )

    body = client.get("/api/certificate").json()
    i2 = next(row for row in body["ledger"] if row["code"] == "I2")
    assert i2["held"] is False
    assert body["emission_withheld"] is True


# ── quiz ─────────────────────────────────────────────────────────────


def test_quiz_next_never_leaks_the_key(client, tmp_path) -> None:
    _seed(tmp_path / "coursec.db")
    body = client.get("/api/quiz/next?student_id=alice").json()

    assert body["item"]["stem"].startswith("Question")
    assert len(body["item"]["options"]) == 2
    assert "key" not in body["item"]
    assert json.dumps(body["item"]) .count("one quarter") <= 1  # only as an option, never labelled


def test_quiz_answer_updates_mastery_and_returns_the_key(client, tmp_path) -> None:
    _seed(tmp_path / "coursec.db")
    item_id = client.get("/api/quiz/next?student_id=alice").json()["item"]["id"]
    before = client.get("/api/quiz/mastery?student_id=alice").json()

    body = client.post(
        "/api/quiz/answer",
        json={"item_id": item_id, "answer": "one quarter", "student_id": "alice"},
    ).json()

    assert body["correct"] is True
    assert body["key"] == "one quarter"
    after = client.get("/api/quiz/mastery?student_id=alice").json()
    assert max(c["mastery"] for c in after["concepts"]) > max(
        c["mastery"] for c in before["concepts"]
    )


def test_wrong_answer_surfaces_the_misconception_and_the_root_cause(client, tmp_path) -> None:
    ids = _seed(tmp_path / "coursec.db")
    # Drive the prerequisite weak so the root-cause walk has somewhere to go.
    with Graph(tmp_path / "coursec.db") as graph:
        sink = DiagnosticSink()
        for _ in range(3):
            learn_pass.record_response(
                graph, sink, student_id="bob", concept_id=ids["prereq"], correct=False
            )

    body = client.post(
        "/api/quiz/answer",
        json={"item_id": ids["item0"], "answer": "twice as faint", "student_id": "bob"},
    ).json()

    assert body["correct"] is False
    assert "inverse-square" in body["misconception"]
    assert body["root_cause"]["name"] == "Luminosity"
    assert body["root_cause"]["depth"] == 1


def test_quiz_answer_404s_on_an_unknown_item(client, tmp_path) -> None:
    _seed(tmp_path / "coursec.db")
    response = client.post("/api/quiz/answer", json={"item_id": "nope", "answer": "x"})
    assert response.status_code == 404


def test_asked_items_are_excluded_from_the_next_question(client, tmp_path) -> None:
    ids = _seed(tmp_path / "coursec.db")
    both = f"{ids['item0']},{ids['item1']}"
    body = client.get(f"/api/quiz/next?student_id=alice&asked={both}").json()
    assert body["item"] is None
    assert "no accepted items left" in body["reason"]


def test_mastery_marks_unobserved_concepts_as_sitting_at_the_prior(client, tmp_path) -> None:
    _seed(tmp_path / "coursec.db")
    body = client.get("/api/quiz/mastery?student_id=nobody").json()
    assert body["prior"] == learn_pass.DEFAULT_PARAMS.p_init
    assert all(c["observed"] is False for c in body["concepts"])
    assert all(c["mastery"] == body["prior"] for c in body["concepts"])
