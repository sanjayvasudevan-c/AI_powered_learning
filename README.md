# CourseC

**A course compiler.** You give it one source chapter (a PDF); it gives you back a graph of the concepts in that chapter — linked to your syllabus, linked to each other as prerequisites, backed by cited evidence — and, from that graph, a lesson, a set of assessment items, and a rendered course booklet, cheat sheet, question paper, and answer key.

The framing is deliberate: source material is the *input language*, the **Concept Graph** is the *IR*, each stage below is a *typed compiler pass*, and the rendered targets are *codegen*. Nothing here is a single long prompt. If a change would turn a pass back into "ask the model and hope," it doesn't belong in this codebase.

```
      PDF                                    Concept Graph (SQLite)                 Targets
  ┌─────────┐   ingest    ┌───────────────────────────────────────────────┐   emit   ┌─────────┐
  │ chapter │ ──────────▶ │ Block, SourceSpan, Concept, Edge, LessonBlock, │ ───────▶ │ booklet │
  │  .pdf   │             │ Item, Misconception, WebEvidence, Verdict, …   │          │ + quiz  │
  └─────────┘             └───────────────────────────────────────────────┘          └─────────┘
```

---

## Why a compiler, not a prompt chain

Ask an LLM to "make a lesson from this PDF" in one shot and you get something that *reads* fine and is *unauditable*: you can't point at a sentence and say why it's true, you can't tell a hallucinated formula from a real one, and re-running it changes the output for reasons no one can name.

Compiling instead of prompting buys three things a single prompt cannot:

- **A typed intermediate representation.** Every fact the system knows is a row in a real schema — a `Concept`, a `SourceSpan`, a `WebEvidence` chunk — not a paragraph of prose one model wrote for another model to re-read.
- **Passes that own their columns.** `ingest` only ever writes `Block`/`SourceSpan`. `compose` only ever writes `LessonBlock`. A pass writing outside its lane is a caught defect (`Graph.add` enforces this at runtime — see [Pass ownership](#pass-ownership)), not a refactor someone gets to next quarter.
- **Diagnostics instead of vibes.** Every pass reports through a structured `Diagnostic` (severity, code, message) rather than printing or swallowing problems. An error-severity diagnostic is a build failure, full stop.

## Table of contents

- [The invariants](#the-invariants)
- [The Concept Graph IR](#the-concept-graph-ir)
- [Pass ownership](#pass-ownership)
- [The pipeline, pass by pass](#the-pipeline-pass-by-pass)
- [Grounding, concretely](#grounding-concretely)
- [Getting started](#getting-started)
- [Testing philosophy](#testing-philosophy)
- [Project layout](#project-layout)
- [Status](#status)

---

## The invariants

Seven rules hold across every pass. A violation is a build failure — never a warning, never a `TODO`.

| | Invariant |
|---|---|
| **I1** | **No unsupported sentence.** Every emitted factual sentence carries ≥1 `evidenced_by` edge and passes entailment against the union of its cited spans. Unentailed → repair (max 2 attempts) → drop → quarantine. |
| **I2** | **Numbers are computed, never generated.** Every numeric literal in emitted material traces to a `SourceSpan`, a cited `WebEvidence` span, or a sandbox execution result. The model phrases explanations; it never originates a value. |
| **I3** | **Provenance is total.** Every node reachable from an emitted document traces to `(file\|url, locator, retrieved_at, content_hash)`. |
| **I4** | **The prerequisite graph is acyclic.** Cycles are broken by a deterministic, logged policy — never silently. |
| **I5** | **Contract before emission.** A concept enters a target only with a contract status attached. MVP contract = 5 slots: definition, intuition, worked example, visual-or-analogy, ≥2 assessment items. |
| **I6** | **Contradiction is surfaced, never resolved.** When evidence contradicts the source, the source stays primary and the divergence is a marked note with both citations — never an overwrite. |
| **I7** | **Licence-clean media only.** Images are a bbox crop of the source, generated diagram code, or nothing. |

These aren't aspirations in a design doc — they're the thing the adversarial test suite exists to break. If the suite passes on first write, it's too weak and gets strengthened before the stage is considered done.

## The Concept Graph IR

Everything the compiler knows lives in one SQLite database, as SQLModel tables. Every node — regardless of type — carries `id`, `created_by_pass`, `content_hash`, `created_at`; `created_by_pass` is immutable after first write.

<details>
<summary><strong>Node types</strong></summary>

| Node | What it holds |
|---|---|
| `Block` | One classified unit of a source page — heading, paragraph, figure, caption, table, equation, or list |
| `SourceSpan` | The exact `(file, page, bbox, char_range, sha256)` a `Block` came from |
| `Concept` | A definition, formula, procedure, theorem, phenomenon, or example, with a salience score and a contract `status` |
| `Alias` | A name that canonicalized into an existing `Concept` rather than becoming its own |
| `SyllabusNode` | One hand-authored curriculum entry a `Concept` may (or may not — abstaining is correct) link to |
| `WebEvidence` | One retrieved, scored, tiered, admitted/weak/rejected chunk of external material |
| `LessonBlock` | One generated contract slot for a `Concept`, as structured sentences with per-sentence citations |
| `Verdict` | The critic's entailed/unsupported/contradicted judgement on one sentence, including repair attempts |
| `ExecResult` | The outcome of executing a formula/worked-example claim as SymPy in a subprocess |
| `Item` | One assessment item (mcq/numeric/short/derivation/application) with its gate history |
| `Misconception` | A named, specific piece of faulty reasoning an MCQ distractor encodes |
| `ItemStats` | An item's `(discrimination, difficulty)` from the synthetic pilot, and whether it was quarantined |
| `Mastery` | A student's per-concept mastery posterior, from Bayesian Knowledge Tracing — one row per update, never overwritten |

</details>

<details>
<summary><strong>Edge kinds</strong></summary>

`Edge` is the one relationship table; `kind` is a closed enum, and *who's allowed to create which kind* is enforced, not just documented:

| Kind | Meaning | Owning pass |
|---|---|---|
| `prerequisite_of` | `A → B`: A must be learned before B | `structure` |
| `part_of` | `Concept → Block`: this concept's definition sits under this heading | `structure` |
| `evidenced_by` | `Concept → WebEvidence` **or** `LessonBlock → SourceSpan\|WebEvidence` — the same relationship at two pipeline stages | `evidence`, `compose` |
| `contradicts` | `WebEvidence → Concept`: this evidence disagrees with the source | `evidence` |
| `assesses` | `Item → Concept` or `Item → Misconception` | `assess` |
| `remediates` | remediation content → the misconception it targets *(declared, unused — D7 shipped mastery tracking and root-cause diagnosis, not generated remediation content; see [`learn`](#learn--bkt-mastery-and-root-cause-propagation))* | `learn` |
| `mastery_of` | `Mastery → Concept` *(declared, unused — `Mastery.concept_id` is a plain FK, the same shape `Item.concept_id` already uses; a redundant edge for the same fact wasn't worth adding)* | `learn` |

</details>

## Pass ownership

`Graph.add()` looks up the object's type (or, for an `Edge`, its `kind`) and rejects the write if `created_by_pass` isn't on the approved list — a pass genuinely *cannot* write outside its column, by construction:

```python
>>> graph.add(Block(created_by_pass="understand", ...))
OwnershipViolation: Block is owned by pass 'ingest', not 'understand'
```

| Pass | Writes | Reads |
|---|---|---|
| `ingest` | `Block`, `SourceSpan` | source files |
| `understand` | `Concept`, `Alias`, `SyllabusNode` | `Block` |
| `structure` | `prerequisite_of`, `part_of` edges | `Concept` |
| `gap` | `GapVector`, `RetrievalBudget` (transient) | `Concept`, `SyllabusNode` |
| `evidence` | `WebEvidence`, `evidenced_by`, `contradicts` | `GapVector` |
| `compose` | `LessonBlock`, `evidenced_by` | dossiers |
| `verify` | `Verdict`, `ExecResult` | `LessonBlock` |
| `assess` | `Item`, `Misconception`, `ItemStats` | `Concept`, `LessonBlock` |
| `emit` | rendered targets | everything, read-only |
| `learn` | `Mastery` | `Item`, responses |

## The pipeline, pass by pass

Run end to end by `coursec build chapter.pdf`. Each pass is independently unit-tested with a scripted LLM backend — none of the tests below need a live model or a network connection.

### `ingest` — PDF → typed blocks

PyMuPDF gives raw text/image blocks; a typography-based heuristic classifies each one (font size against the body-text baseline, bullet-prefix detection, a regex for `Figure N.M` / `Table N.M` captions) into `heading | paragraph | figure | caption | table | equation | list`. Figure captions are bound to their figure by sequence order first, geometric proximity second — an unbound figure is never silent, it gets a `caption_missing` diagnostic. Every `Block` gets a `SourceSpan` with a running character offset across the whole document, so later provenance chains are exact byte ranges, not "somewhere in the PDF."

### `understand` — blocks → canonical concepts, anchored to a syllabus

Two sub-passes:

- **Extraction** is one LLM call *per section*, never per paragraph and never per pair — a chapter has dozens of paragraphs but a handful of subsections, and calling a model in a loop over more than that is exactly the anti-pattern the cost rules forbid.
- **Canonicalization** is LLM-free: candidate concepts are embedded (`name: definition`, local `bge-small`) and agglomeratively clustered. The merge threshold (cosine similarity 0.80) isn't a guess — it's tuned against a committed labelled fixture where the pair that must merge ("Ohm's law" / "V = IR relationship") embeds at 0.855, and the closest false-positive risks ("variance"/"covariance" at 0.764, "hypothesis"/"theory" at 0.773) both sit below 0.78.

Each surviving `Concept` links to **zero or one** `SyllabusNode` via hybrid embedding+lexical retrieval with an abstain threshold — forcing a link when there isn't a good one is worse than reporting a gap.

### `structure` — the prerequisite DAG

An edge only exists when **two independent signals agree**: (a) concept A's definition text actually mentions concept B by name, and (b) an LLM pairwise judgement — run only over the candidate pairs (a) plus same-section co-occurrence produce, never all *O(n²)* pairs — agrees on the same direction. Any cycle that slips through is broken by repeatedly removing its lowest-confidence edge, tie-broken on a deterministic content hash (not a random ID — a random tie-break would make cycle-breaking non-reproducible run to run, defeating the point of a *deterministic* policy).

### `gap` — deciding what's missing, before spending anything on it

A four-field `GapVector` per concept: **coverage** (no syllabus link), **depth** (definition under a word floor), **modality** (no worked example *and* no nearby figure), **prerequisite** (an ancestor is itself uncovered). A `RetrievalBudget` is allocated proportional to that vector under a hard global cap — a fully-covered chapter allocates a *total* budget of zero, because deciding not to search is a valid outcome, not a missing feature. The allocation is provably scale-invariant: doubling the concept count can only shrink any one concept's share, never grow it.

### `evidence` — retrieval that respects the web it's reading

Query synthesis is per gap *dimension* ("Ohm's law worked example", "Ohm's law diagram", "voltage explained" for an uncovered prerequisite), not per concept. `data/sources.yaml` — tier1/tier2/blocklist domains — is data the pass loads, never text baked into a prompt. Fetching is real: `httpx`, `robots.txt` honored via `urllib.robotparser` (not just parsed and ignored), per-domain rate limiting, `BeautifulSoup` strips nav/header/footer and chunks by heading. Admission is a strict policy, not a vibe: present-in-source or corroborated by ≥2 independent tier-1 domains → admitted; a single tier-1 source → `weak`, usable for enrichment but **never as the sole citation for a formula, constant, or definition**; anything else → rejected, with a reason. A numeric mismatch against the source produces a `contradicts` edge — the source is never overwritten.

### `compose` — generation that can't answer from memory

Each of the four generative contract slots (definition, intuition, worked example, visual-or-analogy) is one LLM call constrained to a `Dossier`: the concept's source span, its admitted evidence (best-score-first, truncated by dropping the *worst* first under a token budget), one-line prerequisite summaries. **If the dossier has no grounding material, generation refuses before the backend is ever called** — this is a code-level guard, not a prompt instruction hoping the model behaves, which is what makes "strip the dossier and confirm generation fails" an actual deterministic test rather than a hope about model behavior. Every sentence in the output carries the bracketed ids (`[SPAN:...]`, `[EVID:...]`) it was drawn from. A visual is Mermaid source, never prose describing an image.

### `verify` — a critic that can't see what it's grading, plus real computation

The critic sees one `LessonBlock`'s sentences and, for each, **only that sentence's own cited spans** — looked up fresh by id — never the dossier that produced it. It classifies each sentence `entailed | unsupported | contradicted`; a failing sentence is rewritten against the same citations up to twice, then dropped. Drop every sentence in a slot and the slot is quarantined, the concept marked `partial`. Every judgement — including intermediate repair attempts — is a `Verdict` row, not just the final one.

Separately, `compute.py` takes any worked-example's `formula`/`substitutions`/`claimed_result` and actually executes it — SymPy, in a subprocess, with a timeout — checking the claimed result against what the formula really evaluates to. This is the concrete form invariant I2 takes: a worked example that claims `m·a = 5` when `m=2, a=3` is caught by *running the arithmetic*, not by asking a model to double-check itself.

The origin linter then scans surviving prose for numeric literals and confirms each one traces to a cited span, cited evidence, or an `ExecResult` — anything else is an **error**-severity diagnostic.

### `assess` — items a model can't just answer from the stem

Per concept × Bloom level, one item, typed by the concept's own kind (a `formula` concept gets a `numeric` item; a `definition` gets an `mcq`; etc.). **Every MCQ distractor must link to a real `Misconception` node** — sourced from sentences the critic actually marked `contradicted` upstream (real material, never invented) and an explicit elicitation call; a distractor that can't cite one is dropped, and an item left with none is rejected outright. Four gates, all blocking, run in full even after an early failure so the rejection log shows every reason, not just the first:

1. **Key verification** — solve the item independently 5 times with no access to the key; majority must agree.
2. **Leakage** — answer with *no course context at all*; correct-and-confident means the item is testing trivia, not the chapter.
3. **Single-answer** — every distractor must be independently judged defensibly wrong; any ambiguity rejects the item.
4. **Numeric execution** — a quantitative key must equal the sandboxed computation.

*A gate that never rejects anything is broken, not perfect* — the pipeline asserts the aggregate rejection rate is nonzero.

Surviving items go through a **synthetic pilot**: 12 simulated students on a fixed, known ability grid, each seeded with a misconception profile drawn from real `Misconception` nodes, answer every item; a from-scratch 2PL IRT fit (SciPy `minimize`, ~30 lines — no `py-irt` dependency) recovers `(discrimination, difficulty)` per item and flags degenerate ones for quarantine. This is labelled **screening** everywhere in the code and output — twelve students is nowhere near enough for a calibration claim, and the wording is not allowed to drift.

### `emit` — four real targets and a certificate, read-only over everything else

Document IR here is just the Typst markup each target assembles from the graph, compiled with [`typst`](https://typst.app)'s Python bindings. The **cheat sheet** is a pure template over each `Concept`'s own `SourceSpan` — never a `LessonBlock` — so it makes zero LLM calls, directly or indirectly (asserted on the cache-miss counter, the same way D1's ingest test proves it). The **booklet** walks every contract slot, renders `visual_or_analogy`'s Mermaid source to SVG at build time (via `mermaid.ink`, pluggable like every other external call in this project), crops source figures by their stored bbox with PyMuPDF, and appends a bibliography generated by walking `evidenced_by` edges — never hand-assembled. The **question paper** greedily selects accepted items against a `Blueprint` (total marks, Bloom mix, item-type mix, per-unit weightage); an infeasible blueprint names its exact binding constraint rather than silently returning a paper that's merely close. The **answer key** mirrors the question paper's selection and reads each numeric item's `ExecResult` — already computed and stored back in D5's gate 4 — rather than recomputing anything.

**A build with any error-severity diagnostic produces no PDF at all** — the certificate (`build/certificate.html`) still gets written either way, since it's the artifact that explains *why*.

A fixed compile timestamp is what makes "identical IR → byte-identical PDF" a real, checked property rather than a hope:

```python
def test_identical_ir_and_fixed_timestamp_gives_byte_identical_pdf() -> None:
    first = targets.render_cheat_sheet(graph, [concept], work_dir=tmp_path / "a")
    second = targets.render_cheat_sheet(graph, [concept], work_dir=tmp_path / "b")
    assert first == second
```

### `learn` — BKT mastery and root-cause propagation

`passes/learn.py` is deliberately split into a pure model and thin plumbing around it, the same separation `pilot.py` draws between `fit_2pl` and the pass that calls it. `bkt_update(prior, correct, params)` is one closed-form step of the standard two-state Bayesian Knowledge Tracing HMM — Bayes' rule against the observed response, then the learn-opportunity transition — with no I/O and no graph, so a property test can hold it to the textbook formula directly rather than trusting an integration test to notice a sign error. Every update appends a new `Mastery` row rather than overwriting the last, the same append-only convention `Verdict` uses for D4's critic: the *history* of a student's posterior is data, current mastery is just its latest row.

A wrong answer on concept C doesn't stop at "C is weak" — `diagnose_root_cause` walks `prerequisite_of` edges upstream from C one hop at a time, following the weakest direct prerequisite as long as one is still below `WEAK_THRESHOLD`, and stops at the deepest concept in an unbroken chain of weakness. A solid prerequisite breaks the chain on purpose: its own weak ancestors aren't blamed for a failure the solid concept between them and C would already have caught. Ties are broken on `content_hash`, not the graph's random `id` — the same determinism discipline `structure.py`'s cycle-breaking applies.

`select_next_item` picks the next question adaptively: the accepted `Item` on whichever concept this student's current mastery is lowest on, excluding items already asked this session. `coursec quiz path/to/coursec.db` runs this loop from the terminal; `coursec serve path/to/coursec.db` shells out to `streamlit run` on `ui/quiz_app.py`, a thin session-state wrapper around the same three functions — display and plumbing only, so the only new thing a bug could hide in is wiring, not logic. `ui/quiz_app.py` has its own test (`tests/test_quiz_app.py`) that clicks through it for real via `streamlit.testing.v1.AppTest`, not a mock of the UI layer.

What D7 does *not* build: the `remediates` edge (linking generated remediation content to a `Misconception`) has no remediation-content generator behind it yet, and `Mastery` links to its `Concept` via a plain FK rather than the declared-but-unused `mastery_of` edge kind — see the edge-kind table's notes. The BKT parameters (`p_init`, `p_transit`, `p_slip`, `p_guess`) are illustrative defaults, not fit to any real cohort — the same "screening, not calibration" honesty the pilot insists on for its own numbers applies here too.

### `demo` — the harness: adversarial fixtures, end to end

Every invariant above already has its own per-pass proof — I1 in `tests/test_verify.py`, I2 in `tests/test_compute.py`, I4 in `tests/test_structure.py`, I6 in `tests/test_evidence.py`, the single-answer gate in `tests/test_item_gates.py`. `demo/harness.py` (`coursec demo`) is not a second copy of those tests; it answers a different question — does the *same* planted defect still get caught once the real passes are wired together the way `coursec build` actually wires them, not only inside one pass's own smaller fixture?

Five scenarios, each scored against the specific diagnostic code its invariant promises to raise, never against "the build didn't crash":

- **Real chapter, I1 + I2 together.** Ingests the actual fixture chapter (`tests/fixtures/chapter.pdf`) and hands a real `Block`/`SourceSpan` to a hand-built `Concept`, then runs `compose` → `verify` over it with a scripted backend that plants both an unsupported claim and a wrong worked-example computation. `understand`'s LLM extraction is skipped on purpose here — its canonicalization step always calls the local embedding model, which needs a one-time download this project's own sandboxed CI blocks (the same reason 10 pre-existing tests are environment-gated; see [Status](#status)) — so this scenario stays runnable with no live model and no network.
- **A 3-cycle of conflicting prerequisite signals is broken** (I4) — a direct `structure.break_cycles` call, the same construction `test_structure.py` uses.
- **An ungrounded concept refuses generation outright, without ever calling the backend** (I5's grounding guard) — a poison backend that raises `AssertionError` if invoked at all.
- **An MCQ with a defensible-both-ways distractor is rejected by the single-answer gate** — a full `item_gates.assess_concept` run scored on the resulting `item_rejected` diagnostic actually naming `single_answer`.
- **A low-authority page asserting a wrong constant is rejected as evidence and flagged as a contradiction, never overwriting the source** (I6) — this one *does* need the embedding model (`evidence.py` always scores admitted chunks against a concept embedding), so it shares the same network caveat as the first scenario's skipped extraction step; it stays in the harness anyway; excluding a real invariant to keep the report all-green would be exactly the "loosen the gate so more pass" move CLAUDE.md forbids elsewhere.

A scenario that raises an unexpected exception is reported as its own `error` status, distinct from `fail` (ran clean, didn't catch its defect — the harness's own bug): one flaky or environment-gated scenario should never look identical to a real regression, and `run_demo()` never lets one scenario's exception end the run for the rest. `coursec demo [pdf]` prints a PASS/FAIL/ERROR line with measured counts per scenario and writes `build/demo_report.html`; it exits non-zero unless every scenario passed.

### `web` — the frontend, and a read-mostly API under it

`coursec web` serves a real frontend (`src/coursec/web/static/`) over a FastAPI surface (`src/coursec/web/api.py`) that reads a built Concept Graph. The visual language is **Editorial Ink**: warm paper, one oxblood accent with two semantic hues at matched OKLCH lightness (moss for *held*, ochre for *partial*), Instrument Serif over Newsreader over IBM Plex Mono — and **the margin as a first-class column**, because marginalia is exactly what this compiler's output is. A claim in the body lights its own source note in the margin; a concept's citations render as superscript page marks next to the sentence they support.

Four surfaces, all on real rows rather than mock JSON:

- **Overview** — the landing page, with the ten passes drawn as a press line that a single token travels while each stage inks in.
- **Graph** — the prerequisite DAG laid out by depth, straight from `prerequisite_of` edges; pick a concept and its four contract slots, citations, sandbox execution result and exact `(file, page, char range, sha256)` provenance open in the margin.
- **Quiz** — D7's adaptive loop over HTTP: the item is chosen by lowest BKT posterior, the key is never sent to the browser until an answer is in, and a miss surfaces both the misconception the chosen distractor encodes and the root-cause walk to the deepest still-weak prerequisite.
- **Certificate** — the invariant ledger. **This one is recomputed, never replayed**: a build's `DiagnosticSink` is not persisted, so the API derives each row from the IR — `Verdict` rows, failed `ExecResult` rows, `Item.status`, `ItemStats.quarantined` — and says so in its own `derived_from` field rather than implying it read a log it never saw.

The database is resolved per request, so a build running against the same path appears without a restart, and every endpoint answers `{"available": false}` with the command to fix it rather than 500-ing when nothing has been compiled yet. No build step and no framework: the page is plain HTML, CSS and ES modules served from the package, and everything drawn from the API is escaped before it reaches the DOM — concept names and lesson sentences come out of a PDF someone else wrote.

## Grounding, concretely

The two guarantees above ("empty dossier ⇒ refuses" and "critic never sees the dossier") are each backed by a unit test that doesn't depend on model behavior:

```python
def test_empty_dossier_refuses_without_calling_the_backend() -> None:
    dossier = Dossier(concept_id="c1", prefix="CONCEPT: x\nCOHORT: intro")  # no spans, no evidence

    def backend(model, prompt, params):
        raise AssertionError("backend must never be called for an ungrounded dossier")

    result = compose.generate_slot(dossier, "definition", sink, backend=backend)
    assert result.refused is True
```

```python
def test_critic_never_receives_dossier_content_it_did_not_cite() -> None:
    # concept has admitted evidence containing "UNCITEDMARKER"; the sentence
    # under test cites only its SourceSpan, never that evidence chunk.
    ...
    verify.verify_lesson_block(graph, block, sink, backend=backend)
    assert "UNCITEDMARKER" not in captured_prompts[0]
```

Every pass takes its LLM `backend` as an explicit, required argument — the same shape `llm.call`'s cache wrapper uses. There is no code path where a pass can quietly reach for a live model; tests script deterministic responses, and the one production backend (`anthropic_backend`) fails loudly — never fabricates — when no API key is configured.

## Getting started

```bash
# Python 3.12, managed by uv
uv sync

export ANTHROPIC_API_KEY=sk-...   # needed for understand/structure/compose/verify/assess

uv run coursec build path/to/chapter.pdf
```

`build` runs the full pipeline and prints, per stage: the block-type histogram, concept count and syllabus link/abstain rate, prerequisite edge count, the gap histogram, unsupported/contradicted sentence counts, numeric-origin violations, item generation/rejection counts, and the pilot's discrimination/difficulty spread. It writes `build/graph.html` (a self-contained, offline-viewable render of the concept graph), `build/certificate.html` (the audit trail — always written, pass or fail), and, when no error-severity diagnostic fired, four PDFs under `build/emit/`: `booklet.pdf`, `cheat_sheet.pdf`, `question_paper.pdf`, `answer_key.pdf`.

Without a network-connected search provider, retrieval stops at "no search backend configured" rather than fabricating results — the rest of the pipeline (composition, verification, assessment, rendering) still runs on whatever the source PDF and any evidence already in the graph provide.

Once a build has produced `build/coursec.db`, `uv run coursec quiz build/coursec.db` runs an adaptive quiz from the terminal (BKT-driven item selection, mastery updates, root-cause readout on a miss); `uv run coursec serve build/coursec.db` does the same thing as a Streamlit app.

`uv run coursec web build/coursec.db` serves the frontend at http://127.0.0.1:8000 — the landing page works with no database at all, and the graph, quiz and certificate views fill in as soon as one exists.

`uv run coursec demo` runs the adversarial demo harness against the fixture chapter (or any PDF you pass it) and reports, per scenario, whether every invariant above is actually caught in a real pipeline run — see [`demo`](#demo--the-harness-adversarial-fixtures-end-to-end) above.

## Testing philosophy

Four kinds of test, and every pass writes whichever apply:

- **Unit** — pure functions and schema validation (e.g. domain-tier classification, gap-vector arithmetic).
- **Property** — invariants checked with [Hypothesis](https://hypothesis.readthedocs.io/) over generated input, e.g. *the prerequisite graph is acyclic after cycle-breaking, for any graph*.
- **Golden** — a committed snapshot of `ingest`'s output against a real, openly-licensed fixture chapter (`tests/fixtures/chapter.pdf` — an excerpt of OpenStax *Astronomy 2e*, CC BY 4.0); diffs are reviewed, never blanket-regenerated.
- **Adversarial** — poisoned input the system must catch on purpose: a low-authority page asserting a wrong constant, an MCQ with two defensible answers, a worked example whose arithmetic doesn't hold up.

```bash
make check   # ruff check . && pytest -q
```

230+ tests, 100% branch coverage on `src/coursec/core/` (the coverage target is deliberately scoped there — [see below](#status) for why the rest isn't graded the same way). D7's UI is included in that count, not exempted from it: `tests/test_quiz_app.py` drives `ui/quiz_app.py` for real through `streamlit.testing.v1.AppTest` rather than skipping it as "just a UI."

## Project layout

```
src/coursec/
├── cli.py                  # `coursec build` — wires every pass together
├── core/
│   ├── models.py            # the IR: every SQLModel table, IRNode base, EdgeKind
│   ├── graph.py              # Graph: add/get/neighbors/ancestors/descendants, ownership enforcement
│   ├── diagnostics.py         # Diagnostic, DiagnosticSink
│   ├── llm.py                  # content-addressed LLM call cache
│   ├── embeddings.py            # bge-small wrapper
│   └── anthropic_backend.py      # the one production llm.call backend
├── passes/
│   ├── ingest.py             # PDF → Block/SourceSpan
│   ├── understand.py          # extraction + canonicalization
│   ├── syllabus.py             # syllabus anchoring + coverage report
│   ├── structure.py             # prerequisite DAG
│   ├── gap.py                    # GapVector, RetrievalBudget
│   ├── evidence.py                 # fetch, score, admit, contradict
│   ├── dossier.py                   # grounding assembly for compose
│   ├── compose.py                    # per-slot generation
│   ├── verify.py                      # critic, repair, quarantine
│   ├── compute.py                      # SymPy-in-subprocess execution
│   ├── origin_linter.py                 # I2 enforcement
│   ├── items.py                          # item + misconception generation
│   ├── item_gates.py                      # the 4 blocking gates
│   ├── pilot.py                            # synthetic 2PL screening
│   └── learn.py                             # BKT mastery, root-cause propagation
├── viz/graph_html.py        # pyvis concept-graph render
├── ui/quiz_app.py            # Streamlit adaptive-quiz UI, over passes/learn.py
├── demo/harness.py           # D8: adversarial fixtures through the real pipeline
├── web/
│   ├── api.py                 # FastAPI: read-mostly over the graph, + the quiz endpoints
│   └── static/                 # Editorial Ink frontend — no build step, no framework
└── emit/
    ├── contract.py            # the 5 MVP slots, per-concept status
    ├── mermaid.py               # Mermaid -> SVG (mermaid.ink, pluggable)
    ├── figures.py                # bbox crop of source figures (PyMuPDF)
    ├── bibliography.py             # walks evidenced_by edges
    ├── blueprint.py                  # greedy question-paper selection
    ├── typst_util.py                   # compile + markup escaping
    ├── targets.py                        # booklet, cheat sheet, question paper, answer key
    └── certificate.py                      # build/certificate.html

data/
├── syllabus.yaml            # hand-authored curriculum for the fixture chapter
└── sources.yaml              # tier1/tier2/blocklist domains (data, not prompt text)

tests/                      # one file per pass, plus fixtures/
```

## Status

| Stage | What it does | Status |
|---|---|---|
| Scaffold | uv project, diagnostics, CLI, CI | ✅ |
| Ingest | PDF → classified blocks | ✅ |
| Understand + syllabus | concept extraction, canonicalization, anchoring | ✅ |
| Structure | prerequisite DAG | ✅ |
| Gap + evidence | budget allocation, real web retrieval | ✅ |
| Compose + verify | grounded generation, critique, computation | ✅ |
| Assess | items, gates, synthetic pilot | ✅ |
| Emit | Typst rendering — booklet, cheat sheet, question paper, answer key, certificate | ✅ |
| Learn | terminal + Streamlit adaptive quiz, Bayesian Knowledge Tracing mastery, root-cause readout | ✅ |
| Demo harness | adversarial fixtures, end-to-end measured results | ✅ |
| Web | FastAPI over the graph + the Editorial Ink frontend (overview, graph, quiz, certificate) | ✅ |

The evidence pass has no configured search-API provider in this environment, so a live `coursec build` runs real retrieval mechanics (fetch, robots.txt, scoring, admission) against whatever URLs a `search` callable hands it, but ships no default search backend — wiring one in is the one piece needed to take this from "correct machinery" to "actually crawling the web" end to end.
