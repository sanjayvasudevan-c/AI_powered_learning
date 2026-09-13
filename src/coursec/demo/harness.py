"""D8: the demo harness — deliberately adversarial input run through the
real pipeline, end to end, scored against the specific diagnostic each
invariant promises to raise.

Every invariant here already has its own per-pass proof (I1 in
tests/test_verify.py, I2 in tests/test_compute.py, I4 in
tests/test_structure.py, I6 in tests/test_evidence.py, the single-answer
gate in tests/test_item_gates.py, ...) — this file is not a second copy of
those. It exists to answer a different question: does the *same* defect
still get caught once real passes are wired together the way `coursec
build` actually wires them, rather than only inside one pass's own,
smaller, constructed fixture?

Two honestly-distinguished kinds of scenario:

- One (`_scenario_real_chapter_defects_caught`) ingests the real fixture
  chapter (tests/fixtures/chapter.pdf) and hands a real `Block`/
  `SourceSpan` to a hand-built `Concept`, then runs compose -> verify over
  it — real ingest, real grounding text, scripted generation/critique.
  `understand`'s LLM extraction is skipped on purpose: its canonicalization
  step (understand.py's Pass B) always calls the local embedding model,
  which needs a one-time Hugging Face download this sandbox's network
  policy blocks — the same reason 10 pre-existing tests fail here (see
  tests/test_understand.py, tests/test_syllabus.py). Skipping extraction
  keeps this scenario runnable without a live model *or* network access,
  at the cost of not exercising extraction itself — a pass with its own,
  separate tests.
- The rest build a small constructed graph, the same way each pass's own
  test file does, for an invariant that isn't about document flow at all
  (I4's cycle-breaking, I5's grounding guard, the single-answer gate) — or,
  for I6, one that *does* need the embedding model (evidence.py always
  scores admitted chunks against a concept embedding) and so shares the
  same network-sandbox caveat as the tests above; it is included anyway,
  since excluding a real invariant to keep the report all-green would be
  exactly the "loosen the gate so more pass" move CLAUDE.md forbids.

`run_demo()` never lets one scenario's exception silently end the run: an
unexpected exception is caught and reported as its own "error" status,
distinct from a scenario that ran clean and simply failed to catch its
defect (a "fail" — the harness's own bug). `coursec demo` exits non-zero on
either.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from jinja2 import Template

from coursec.core.diagnostics import DiagnosticSink
from coursec.core.graph import Graph
from coursec.core.models import Block, BlockType, Concept, ConceptType, SourceSpan
from coursec.passes import compose as compose_pass
from coursec.passes import evidence as evidence_pass
from coursec.passes import item_gates
from coursec.passes import structure as structure_pass
from coursec.passes import verify as verify_pass
from coursec.passes.evidence import Fetcher
from coursec.passes.gap import GapVector, RetrievalBudget
from coursec.passes.ingest import ingest_pdf
from coursec.passes.understand import _span_id_for_block

DEFAULT_FIXTURE = Path("tests/fixtures/chapter.pdf")


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    invariant: str
    expected_code: str
    status: str  # "pass" | "fail" | "error"
    measured: dict[str, int] = field(default_factory=dict)
    detail: str = ""


@dataclass(frozen=True)
class DemoReport:
    pdf: str
    scenarios: list[ScenarioResult]

    @property
    def all_passed(self) -> bool:
        return all(s.status == "pass" for s in self.scenarios)


def _hand_built_concept(graph: Graph, name: str, text: str, concept_type: ConceptType) -> Concept:
    """A `Concept` grounded in a hand-built `Block`/`SourceSpan` pair, the
    same construction tests/conftest.py's `add_block_with_span` uses — kept
    local here since production code shouldn't import test helpers."""
    content_hash = f"demo:{name}"
    block = graph.add(
        Block(
            created_by_pass="ingest",
            content_hash=content_hash,
            file_id="demo.pdf",
            page=0,
            bbox=[0.0, 0.0, 1.0, 1.0],
            text=text,
            block_type=BlockType.paragraph,
        )
    )
    graph.add(
        SourceSpan(
            created_by_pass="ingest",
            content_hash=content_hash,
            block_id=block.id,
            file_id="demo.pdf",
            page=0,
            bbox=[0.0, 0.0, 1.0, 1.0],
            char_range=[0, len(text)],
            sha256=content_hash,
        )
    )
    return graph.add(
        Concept(
            created_by_pass="understand",
            content_hash=content_hash,
            name=name,
            concept_type=concept_type,
            definition_span_id=_span_id_for_block(graph, block.id),
        )
    )


def _count(sink: DiagnosticSink, code: str) -> int:
    return sum(1 for d in sink.all() if d.code == code)


# --- A: real chapter, I1 + I2 together, over real ingested text -------------


def _scenario_real_chapter_defects_caught(pdf: Path) -> ScenarioResult:
    sink = DiagnosticSink()
    with Graph() as graph:
        blocks = ingest_pdf(pdf, graph, sink)
        paragraph = next(
            b for b in blocks if b.block_type == BlockType.paragraph and len(b.text.split()) > 8
        )
        concept = graph.add(
            Concept(
                created_by_pass="understand",
                content_hash="demo:real-chapter-concept",
                name="a real-chapter concept",
                concept_type=ConceptType.formula,
                definition_span_id=_span_id_for_block(graph, paragraph.id),
            )
        )

        def backend(model: str, prompt: str, params: dict) -> str:
            ids = (
                re.findall(r"\[(SPAN:[a-f0-9]+)\]", prompt)
                + re.findall(r"\[(EVID:[a-f0-9]+)\]", prompt)
            )[:1]
            if "DOSSIER:" in prompt:
                if '"worked_example" section' in prompt:
                    return json.dumps(
                        {
                            "refused": False,
                            "sentences": [
                                {"text": "m*a with m=2, a=3 gives 5.", "evidence_ids": ids}
                            ],
                            "computation": {
                                "formula": "m*a",
                                "substitutions": {"m": 2.0, "a": 3.0},
                                "claimed_result": 5.0,  # wrong: really 6.0
                            },
                        }
                    )
                if '"intuition" section' in prompt:
                    return json.dumps(
                        {
                            "refused": False,
                            "sentences": [
                                {
                                    "text": (
                                        "PLANTED_UNSUPPORTED_CLAIM: a claim the cited "
                                        "text does not actually make."
                                    ),
                                    "evidence_ids": ids,
                                }
                            ],
                        }
                    )
                return json.dumps(
                    {
                        "refused": False,
                        "sentences": [{"text": "A grounded sentence.", "evidence_ids": ids}],
                    }
                )
            if "checking whether a sentence is entailed" in prompt:
                classification = (
                    "unsupported" if "PLANTED_UNSUPPORTED_CLAIM" in prompt else "entailed"
                )
                return json.dumps({"classification": classification})
            if "Rewrite the sentence" in prompt:
                return json.dumps({"text": "PLANTED_UNSUPPORTED_CLAIM: still unsupported."})
            return json.dumps({"prerequisite": "neither", "confidence": 0.5})

        lesson_blocks = compose_pass.compose_concept(graph, concept, sink, backend=backend)
        for block in lesson_blocks:
            verify_pass.verify_lesson_block(graph, block, sink, backend=backend)
            verify_pass.run_compute_check(graph, block, sink)

        grounding_excerpt = paragraph.text.strip()[:80]

    caught = (
        _count(sink, "sentence_dropped_unsupported") > 0 and _count(sink, "compute_divergence") > 0
    )
    return ScenarioResult(
        name="real chapter: a planted unsupported claim is dropped and a wrong "
        "worked-example computation is caught",
        invariant="I1 (no unsupported sentence) + I2 (numbers are computed, not generated)",
        expected_code="sentence_dropped_unsupported, compute_divergence",
        status="pass" if caught else "fail",
        measured={
            "unsupported_sentences_dropped": _count(sink, "sentence_dropped_unsupported"),
            "compute_divergences": _count(sink, "compute_divergence"),
        },
        detail=f"grounded in real chapter text: {grounding_excerpt!r}...",
    )


# --- B: prerequisite cycle broken deterministically (I4) --------------------


def _scenario_cycle_broken_deterministically() -> ScenarioResult:
    sink = DiagnosticSink()
    candidates = [
        structure_pass.EdgeCandidate("A", "B", 0.9, content_hash="A:B"),
        structure_pass.EdgeCandidate("B", "C", 0.8, content_hash="B:C"),
        structure_pass.EdgeCandidate("C", "A", 0.3, content_hash="C:A"),  # weakest signal
    ]
    surviving = structure_pass.break_cycles(candidates, sink)
    acyclic = structure_pass._find_cycle(surviving) is None
    caught = acyclic and _count(sink, "prerequisite_cycle_broken") > 0
    removed = {c.content_hash for c in candidates} - {e.content_hash for e in surviving}
    return ScenarioResult(
        name="a 3-cycle of conflicting prerequisite signals is broken, not left in the graph",
        invariant="I4 (the prerequisite graph is acyclic)",
        expected_code="prerequisite_cycle_broken",
        status="pass" if caught else "fail",
        measured={
            "candidate_edges": len(candidates),
            "surviving_edges": len(surviving),
            "cycle_breaks_logged": _count(sink, "prerequisite_cycle_broken"),
        },
        detail=f"removed the lowest-confidence edge in the cycle: {sorted(removed)}",
    )


# --- C: empty dossier refuses without ever calling the backend (I5) --------


def _scenario_empty_dossier_refuses() -> ScenarioResult:
    sink = DiagnosticSink()
    with Graph() as graph:
        concept = graph.add(
            Concept(
                created_by_pass="understand",
                content_hash="demo:no-grounding",
                name="an ungrounded concept",
                concept_type=ConceptType.definition,
                definition_span_id=None,
            )
        )

        def poison_backend(model: str, prompt: str, params: dict) -> str:
            raise AssertionError("backend must never be called for an ungrounded dossier")

        lesson_blocks = compose_pass.compose_concept(graph, concept, sink, backend=poison_backend)

    caught = lesson_blocks == [] and _count(sink, "generation_refused_empty_dossier") == 4
    return ScenarioResult(
        name="a concept with no source span and no evidence refuses generation outright",
        invariant="I5 (contract before emission) / the grounding guard compose relies on",
        expected_code="generation_refused_empty_dossier",
        status="pass" if caught else "fail",
        measured={
            "lesson_blocks_written": len(lesson_blocks),
            "refusals_logged": _count(sink, "generation_refused_empty_dossier"),
        },
        detail="the backend raises if called at all — no exception means it never was",
    )


# --- D: an indefensible MCQ distractor is rejected, not shipped -------------


def _scenario_indefensible_distractor_rejected() -> ScenarioResult:
    sink = DiagnosticSink()
    with Graph() as graph:
        concept = _hand_built_concept(
            graph,
            "electric potential difference",
            "Electric potential difference between two points is the work done per unit "
            "charge moving a charge between them; it is measured in volts.",
            ConceptType.definition,
        )

        def backend(model: str, prompt: str, params: dict) -> str:
            if "List" in prompt and "misconceptions" in prompt.lower():
                return json.dumps(["confuses volts with amps, since both are electrical units"])
            if prompt.startswith("Write one"):
                return json.dumps(
                    {
                        "stem": "Which unit measures electric potential difference?",
                        "key": "volt",
                        # The planted defect: a distractor a reasonable person could
                        # also call correct — gate 3 exists exactly to catch this.
                        "distractors": [
                            {"text": "volt (also technically correct)", "misconception_index": 0}
                        ],
                    }
                )
            if "no course material" in prompt:
                return json.dumps({"answer": "not sure", "confidence": 0.1})
            if "defensibly_wrong" in prompt or "defensibly WRONG" in prompt:
                return json.dumps({"defensibly_wrong": False})
            if "Solve this question" in prompt:
                return json.dumps({"answer": "volt"})
            return json.dumps({"answer": ""})

        accepted, rejected_count = item_gates.assess_concept(graph, concept, sink, backend=backend)

    caught = (
        len(accepted) == 0
        and rejected_count > 0
        and any(
            d.code == "item_rejected" and "single_answer" in d.message for d in sink.all()
        )
    )
    return ScenarioResult(
        name="an MCQ with a defensible-both-ways distractor is rejected by the single-answer gate",
        invariant="assess gate 3 (single answer) — item_gates.gate_single_answer",
        expected_code="item_rejected",
        status="pass" if caught else "fail",
        measured={"items_accepted": len(accepted), "items_rejected": rejected_count},
        detail="every generated item shared the same indefensible distractor on purpose",
    )


# --- E: contradicting low-authority evidence is surfaced, not overwritten (I6) ---


def _scenario_contradiction_surfaced_not_overwritten() -> ScenarioResult:
    sink = DiagnosticSink()
    with Graph() as graph, tempfile.TemporaryDirectory() as tmp:
        concept = _hand_built_concept(
            graph,
            "speed of light",
            "the speed of light is 300000 km/s in a vacuum " * 4,
            ConceptType.formula,
        )
        definition_before = graph.session.get(Concept, concept.id).definition_span_id

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="")
            return httpx.Response(
                200,
                text=(
                    "<html><body><h1>Speed of light</h1>"
                    "<p>the speed of light is 999999999 km/s</p></body></html>"
                ),
            )

        fetcher = Fetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
        sources_path = Path(tmp) / "sources.yaml"
        sources_path.write_text("tier1: []\ntier2: [low-authority.example]\nblocklist: []\n")

        gap_vectors = {concept.id: GapVector(coverage=0, depth=0, modality=1, prerequisite=0)}
        budgets = {concept.id: RetrievalBudget(concept.id, queries=5)}

        written = evidence_pass.retrieve_evidence(
            graph,
            [concept],
            gap_vectors,
            budgets,
            sink,
            search=lambda q: ["https://low-authority.example/wrong"],
            fetcher=fetcher,
            sources_path=sources_path,
        )

        concept_after = graph.session.get(Concept, concept.id)
        definition_after = concept_after.definition_span_id
        source_text_after = evidence_pass._definition_text(graph, concept_after)

    caught = (
        any(w.admission == evidence_pass.REJECTED for w in written)
        and definition_after == definition_before
        and "300000" in source_text_after
        and _count(sink, "evidence_contradiction") > 0
    )
    return ScenarioResult(
        name="a low-authority page asserting a wrong constant is rejected as evidence, and "
        "flagged as a contradiction — the source is never overwritten",
        invariant="I6 (contradiction is surfaced, never resolved)",
        expected_code="evidence_contradiction",
        status="pass" if caught else "fail",
        measured={
            "evidence_chunks_written": len(written),
            "contradictions_logged": _count(sink, "evidence_contradiction"),
        },
        detail="requires the local embedding model — see module docstring's network caveat",
    )


# Every scenario takes no arguments except the one grounded in the real
# fixture, which needs to know which PDF to ingest — `run_demo` special-cases
# it rather than inspecting each callable's signature.
_PDF_SCENARIO = _scenario_real_chapter_defects_caught
_OTHER_SCENARIOS = (
    _scenario_cycle_broken_deterministically,
    _scenario_empty_dossier_refuses,
    _scenario_indefensible_distractor_rejected,
    _scenario_contradiction_surfaced_not_overwritten,
)


def _run_scenario(name: str, fn) -> ScenarioResult:
    """Never let one scenario's exception end the run early — an unexpected
    failure is its own "error" status, not a crash."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - deliberately broad: see module docstring
        return ScenarioResult(
            name=name,
            invariant="unknown — scenario raised before it could self-report",
            expected_code="",
            status="error",
            detail=f"{type(exc).__name__}: {exc}",
        )


def run_demo(pdf: Path = DEFAULT_FIXTURE) -> DemoReport:
    results = [_run_scenario(_PDF_SCENARIO.__name__, lambda: _PDF_SCENARIO(pdf))]
    results += [_run_scenario(fn.__name__, fn) for fn in _OTHER_SCENARIOS]
    return DemoReport(pdf=str(pdf), scenarios=results)


_TEMPLATE = Template(
    """<!doctype html>
<html><head><meta charset="utf-8"><title>Demo Harness Report</title>
<style>
body { font-family: system-ui, sans-serif; margin: 2rem; color: #111; }
table { border-collapse: collapse; width: 100%; margin-bottom: 1.5rem; }
td, th { border: 1px solid #ccc; padding: 4px 10px; text-align: left; vertical-align: top; }
.pass { color: #087f23; font-weight: bold; }
.fail { color: #c62828; font-weight: bold; }
.error { color: #a15c00; font-weight: bold; }
</style></head>
<body>
<h1>Demo Harness Report</h1>
<p>Fixture: {{ pdf }}</p>
<p>{{ passed }}/{{ total }} scenarios passed.</p>
<table>
<tr><th>Status</th><th>Invariant</th><th>Scenario</th><th>Measured</th><th>Detail</th></tr>
{% for s in scenarios -%}
<tr><td class="{{ s.status }}">{{ s.status | upper }}</td>
<td>{{ s.invariant }}</td>
<td>{{ s.name }}</td>
<td>{% for k, v in s.measured.items() %}{{ k }}={{ v }}<br>{% endfor %}</td>
<td>{{ s.detail }}</td></tr>
{% endfor -%}
</table>
</body></html>
"""
)


def render_demo_report_html(report: DemoReport) -> str:
    passed = sum(1 for s in report.scenarios if s.status == "pass")
    return _TEMPLATE.render(
        pdf=report.pdf, scenarios=report.scenarios, passed=passed, total=len(report.scenarios)
    )


def write_demo_report(path: Path, report: DemoReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_demo_report_html(report), encoding="utf-8")
