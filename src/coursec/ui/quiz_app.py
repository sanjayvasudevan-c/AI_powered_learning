"""The Streamlit face of `passes/learn.py` — `coursec serve` runs this file
under `streamlit run`.

All of the actual logic — which item to ask next, the BKT update, root-cause
diagnosis — lives in `passes/learn.py` and is unit/property-tested there
without a browser. This file is display and `st.session_state` plumbing
only, on purpose: a pass a test can hold to a formula, wired to a UI that a
test can only click through (`tests/test_quiz_app.py`, via
`streamlit.testing.v1.AppTest`).

Reads its target database and student id from `COURSEC_DB_PATH` /
`COURSEC_STUDENT_ID` — set by `coursec serve`, or directly by
`AppTest`-based tests, which exec this module in-process the same way
`streamlit run` does.
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from sqlmodel import select

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Concept
from coursec.passes import learn

DB_PATH = Path(os.environ.get("COURSEC_DB_PATH", "build/coursec.db"))
STUDENT_ID = os.environ.get("COURSEC_STUDENT_ID", "student")

st.set_page_config(page_title="CourseC quiz")

if "graph" not in st.session_state:
    st.session_state.graph = Graph(DB_PATH)
    st.session_state.sink = DiagnosticSink()
    st.session_state.asked = frozenset()
    st.session_state.current_item_id = None
    st.session_state.feedback = None

graph: Graph = st.session_state.graph
sink: DiagnosticSink = st.session_state.sink

st.title("CourseC — adaptive quiz")
st.caption(f"student: {STUDENT_ID}")

if st.session_state.current_item_id is None:
    next_item = learn.select_next_item(graph, STUDENT_ID, asked_item_ids=st.session_state.asked)
    st.session_state.current_item_id = next_item.id if next_item else None

item_id = st.session_state.current_item_id

if item_id is None:
    st.success("No more accepted items to ask — session complete.")
else:
    item = graph.get(item_id)
    concept = graph.get(item.concept_id)
    st.subheader(f"[{concept.name}] {item.stem}")

    if item.item_type == "mcq":
        options = learn.mcq_options(item)
        choice = st.radio("Answer", [text for text, _ in options], key=f"choice-{item.id}")
    else:
        choice = st.text_input("Answer", key=f"answer-{item.id}")

    if st.button("Submit", key=f"submit-{item.id}"):
        if item.item_type == "mcq":
            correct = dict(learn.mcq_options(item)).get(choice, False)
        else:
            correct = choice.strip().lower() == item.key.strip().lower()

        learn.record_response(
            graph, sink, student_id=STUDENT_ID, concept_id=item.concept_id, correct=correct
        )
        st.session_state.asked = st.session_state.asked | {item.id}

        if correct:
            st.session_state.feedback = "Correct."
        else:
            root = learn.diagnose_root_cause(graph, item.concept_id, STUDENT_ID)
            if root.concept_id != item.concept_id:
                root_concept = graph.get(root.concept_id)
                st.session_state.feedback = (
                    f"Incorrect (answer: {item.key}). This traces back to "
                    f"'{root_concept.name}' — review that first."
                )
            else:
                st.session_state.feedback = f"Incorrect. The answer was: {item.key}"

        st.session_state.current_item_id = None
        st.rerun()

if st.session_state.feedback:
    st.info(st.session_state.feedback)

concepts = graph.session.exec(select(Concept)).all()
if concepts:
    st.divider()
    st.subheader("Mastery so far")
    rows = sorted(
        ((c.name, learn.latest_mastery(graph, c.id, STUDENT_ID)) for c in concepts),
        key=lambda row: row[1],
    )
    for name, probability in rows:
        st.progress(probability, text=f"{name}: {probability:.0%}")
