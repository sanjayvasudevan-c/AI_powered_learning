# PROMPTS.md — paste one block at a time into Claude Code

`CLAUDE.md` is binding for every stage. Do not paste two stages at once.
Paste the block between the `---8<---` markers.

Every stage ends with this, so it is not repeated below:

> Run `make check`. Paste the real output, not a summary.
> Green → commit `D<n>: <summary>`, push, print the SHA.
> Red → HALT, state what failed, do not commit, do not weaken a test.
> Spec ambiguity → HALT and ask, listing the options and the tradeoff.

---

## D0 — Scaffold and gate harness

---8<---
Read CLAUDE.md fully before starting.

Scaffold the CourseC repository.

Build:
- `uv` project, `src/coursec/` package layout, `pyproject.toml`
- dependencies: sqlmodel, typer, pydantic v2, pymupdf, httpx, sympy,
  sentence-transformers, streamlit, jinja2, pyyaml; dev: ruff, pytest,
  pytest-cov, hypothesis
- `Makefile` with a `check` target running `ruff check .` then `pytest -q`
- `.github/workflows/ci.yml` running the same `make check` on push
- `.gitignore` excluding `build/`, `data/`, `.cache/`, `.env`, `*.db`
- `RUN_LOG.md` with a header and an empty stage table
- `RESULTS.md` with an empty metrics table
- `src/coursec/cli.py` — typer app with three stub commands:
  `build <pdf>`, `serve`, `quiz`. Each prints a not-implemented message
  and exits 1. Do not stub them as passing.
- `src/coursec/core/__init__.py`, `src/coursec/core/diagnostics.py` with a
  `Diagnostic` model (`severity: Literal["error","warning","info"]`, `code`,
  `message`, `node_id`, `pass_name`) and a `DiagnosticSink` that collects them
  and exposes `has_errors()`.

Tests:
- `DiagnosticSink.has_errors()` is True iff an error-severity diagnostic exists
- CLI stubs exit non-zero

Done when: `make check` is green on a typed, empty-but-real package, and CI is
green on the first push. Print the CI run URL.
---8<---

---

## D1 — IR, ingest, and the call cache

---8<---
Read CLAUDE.md. This stage owns the `ingest` column in §4.

Build the IR and the ingest pass.

**IR** — `src/coursec/core/models.py`, SQLModel tables:
`Block, SourceSpan, Concept, Alias, SyllabusNode, WebEvidence, LessonBlock,
Item, Misconception, Verdict, ExecResult, Mastery, Edge`.
`Edge` has `kind: EdgeKind` enum =
`prerequisite_of | part_of | evidenced_by | contradicts | assesses | remediates | mastery_of`.
Every node carries `id, created_by_pass, content_hash, created_at`.

`src/coursec/core/graph.py` — a `Graph` wrapper over the SQLite session with:
`add`, `get`, `neighbors`, `ancestors`, `descendants` (recursive CTE),
`subgraph(concept_ids)`, `to_json`, `from_json`.
Enforce CLAUDE.md §4 ownership: `add` raises if `created_by_pass` is not the
pass declared as owner for that node type.

**Ingest** — `src/coursec/passes/ingest.py`:
PyMuPDF → `Block` rows with `(page, bbox, text)`, classified as
`heading | paragraph | figure | caption | table | equation | list`.
Bind captions to figures: numbering match first, geometric proximity second.
Emit a `caption_missing` diagnostic for any unbound figure — silence is not
allowed.
Every Block gets a `SourceSpan(file_id, page, bbox, char_range, sha256)`.

**Cache** — `src/coursec/core/llm.py`:
a `call(model, prompt, **params)` wrapper keyed by `sha256(model, prompt,
sorted params)` into `.cache/llm/`. Expose a module-level counter of
cache-miss calls for testing.

Tests:
- `to_json` / `from_json` round-trip identity on a populated graph
- inserting an Edge referencing a missing node raises
- `created_by_pass` is immutable after write
- ownership violation raises
- golden: committed Block snapshot for one fixture PDF chapter
  (`tests/fixtures/chapter.pdf` — use a born-digital, openly licensed chapter)
- every figure Block has a bound caption or a `caption_missing` diagnostic
- **cache: run ingest twice on identical input, assert the cache-miss counter
  is zero on the second run**

Done when all tests pass and `coursec build tests/fixtures/chapter.pdf` writes
blocks to SQLite and prints a block-type histogram.
---8<---

---

## D2 — Concepts, syllabus anchoring, prerequisite DAG

---8<---
Read CLAUDE.md. This stage owns `understand` and `structure` in §4.

**Extraction** — `src/coursec/passes/understand.py`, two passes:
Pass A, per chunk: emit candidates with `name, type ∈ {definition, formula,
procedure, theorem, phenomenon, example}, definition_span_id, salience`.
Pass B, global: canonicalize by embedding clustering with `bge-small`,
agglomerative, threshold tuned on a labelled fixture. Merge aliases into
`Alias` rows. One `Concept` may have `part_of` edges to several sections.

**Syllabus anchoring** — write `data/syllabus.yaml` by hand for the fixture
chapter (20–40 unit/topic entries). Load into `SyllabusNode`. Link each Concept
to zero or one SyllabusNode via hybrid retrieval (embedding + lexical) with an
**abstain threshold**. Abstaining is a correct outcome; never force a link.

Emit `coverage_report`: SyllabusNodes with no linked Concept are coverage gaps.

**Prerequisite DAG** — `src/coursec/passes/structure.py`.
Two fused signals: (a) concept A's definition span mentions concept B;
(b) an LLM pairwise judgement run **only over candidate pairs** produced by (a)
plus co-occurrence — never all pairs. Require both signals to create an edge.
Break cycles by removing the lowest-confidence edge, deterministic tie-break on
`(confidence, edge_id)`. Log every break as a diagnostic.

**Visualization** — `src/coursec/viz/graph_html.py`: render the concept graph to
a standalone HTML file (pyvis or graphviz). Colour nodes by contract status
once D4 lands; for now colour by `type`.

Tests:
- no two surviving Concepts exceed the merge threshold in cosine similarity
- "Ohm's law" and "V = IR relationship" merge; "variance" and "covariance" do not
- property: the prerequisite graph is acyclic after cycle breaking
- **adversarial: delete three known topics from the fixture source; assert
  exactly those three are reported as coverage gaps and nothing else is**

Done when `coursec build` prints concept count, link rate, abstain rate, gap
count, and writes `build/graph.html`.
---8<---

---

## D3 — Gap engine and evidence retrieval

---8<---
Read CLAUDE.md. This stage owns `gap` and `evidence` in §4.

**Gap engine** — `src/coursec/passes/gap.py`.
Per Concept, compute a four-field `GapVector`:
`coverage` (unlinked syllabus node), `depth` (below a token floor, or no formal
statement), `modality` (no worked example, no visual), `prerequisite` (an
ancestor is itself uncovered).
Allocate `RetrievalBudget` proportional to the vector under a global cap.
Assert scale invariance: doubling the input does not double per-concept spend.

**Retrieval** — `src/coursec/passes/evidence.py`.
Query synthesis is **per gap dimension**, not per concept:
`"{concept} worked example"`, `"{concept} derivation"`, `"{concept} diagram"`,
`"{prerequisite} explained"`.

Write `data/sources.yaml` as data, not prompt text:
- tier1: openstax.org, nptel.ac.in, ocw.mit.edu, khanacademy.org,
  en.wikipedia.org, commons.wikimedia.org, arxiv.org, `*.edu`, `*.gov`
- tier2: university lecture-note domains, established technical publications
- blocklist: scraped-answer sites, content farms, SEO aggregators

Fetch with httpx, respect robots.txt, rate-limit per domain, strip navigation,
chunk preserving heading context, record `url, retrieved_at, content_hash`.

**Scoring**:
`authority_prior * cos(chunk, concept_emb) * pedagogical_fit * recency^volatility - redundancy_penalty`

**Admission policy** (autonomous, CLAUDE.md I6):
- present in source → admitted
- corroborated by ≥2 independent tier-1 domains → admitted
- single tier-1 → `weak`; usable for enrichment, **never** for a formula,
  constant, or definition
- otherwise → rejected, logged with the reason

Contradictions create a `contradicts` edge. Never overwrite the source.

Tests:
- **a fully-covered fixture allocates a total budget of zero** — the system must
  be able to decide not to search
- blocklisted domains are never fetched
- a `weak` chunk cannot be admitted as the sole evidence for a formula
- adversarial: a low-authority page asserting a wrong constant produces a
  `contradicts` edge, not an overwrite

Done when `coursec build` prints the gap histogram and an admission log
(admitted / weak / rejected with reasons).
---8<---

---

## D4 — Compose, verify, compute

---8<---
Read CLAUDE.md. This stage owns `compose` and `verify` in §4.
Invariants I1 and I2 are implemented here. Treat them as the point of the stage.

**Dossier** — `src/coursec/passes/dossier.py`: per Concept, assemble source
spans + admitted evidence + prerequisite one-line summaries + cohort level +
unmet contract slots. Enforce a token budget with deterministic truncation
(drop evidence by ascending score, never randomly). The prefix must be
**byte-stable across runs** — assert this.

**Generator** — `src/coursec/passes/compose.py`: one call per contract slot
(definition, intuition, worked example, visual-or-analogy), constrained to the
dossier. Output is structured: each sentence carries the evidence IDs it used.
Visuals are emitted as **Mermaid source**, never as prose describing an image.
A refusal is a valid output and is plumbed through as a diagnostic, not
swallowed.

**Critic** — separate call. Sees the LessonBlock and **only its cited spans**,
not the dossier. Returns each sentence classified
`entailed | unsupported | contradicted`. Repair pass max 2 attempts, then drop
the sentence. If dropping empties a contract slot, mark the concept `partial`
and quarantine the slot. Record every judgement in `Verdict`.

**Compute** — `src/coursec/passes/compute.py`: every formula and worked example
is emitted as SymPy and executed in a subprocess with a timeout and no network.
Check symbolic equivalence against the source-stated form and numerical
agreement on randomized substitutions. Store `ExecResult`.

**Origin linter** — scan emitted prose for numeric literals. Any literal not
traceable to a SourceSpan, a cited WebEvidence span, or an ExecResult is an
`error` diagnostic (I2).

Tests:
- **strip the dossier and assert generation fails** rather than answering from
  parametric memory — this is the test that proves grounding
- the Critic never receives dossier content (assert on the prompt payload)
- property: no emitted sentence lacks an `evidenced_by` edge
- **adversarial: a fixture with one deliberately wrong constant in the source is
  caught by the compute check and produces a divergence note**
- origin linter catches an injected unsourced number

Done when `coursec build` prints unsupported-sentence count, contradicted count,
numeric-origin violations, and quarantine count.
---8<---

---

## D5 — Items, misconceptions, synthetic pilot

---8<---
Read CLAUDE.md. This stage owns `assess` in §4.

**Generation** — `src/coursec/passes/items.py`. Per Concept × Bloom level,
generate items typed `mcq | numeric | short | derivation | application`.

**Every MCQ distractor must link to a `Misconception` node** naming the specific
faulty reasoning it encodes. Sources for misconceptions: the material's own
common-errors text, sentences the Critic marked `contradicted` in D4, and an
explicit misconception-elicitation call. A distractor with no linked
Misconception is rejected and regenerated. This link is what makes D7 diagnostic
rather than merely adaptive — do not shortcut it.

**Gates** (all blocking, `src/coursec/passes/item_gates.py`):
1. Key verification — solve independently, n=5 self-consistency; the majority
   must match the key, else reject.
2. Leakage test — answer the item with **no context**. Correct-and-confident
   means the item tests trivia, not the concept → reject.
3. Single-answer — adjudicate each distractor as defensibly wrong; any ambiguity
   → reject.
4. Numeric execution — quantitative keys must equal the sandbox result (I2).

**Assert the rejection rate is non-zero.** A gate that never rejects is broken,
not perfect.

**Synthetic pilot** — `src/coursec/passes/pilot.py`. Build 12 simulated students
across an ability range, each seeded with a misconception profile drawn from the
`Misconception` nodes. Each answers every surviving item. Fit a 2PL IRT model
(py-irt, or a ~30-line MLE) to the response matrix to obtain `(a, b)` per item.
Quarantine items with near-zero discrimination or degenerate difficulty.

Label this **screening**, not calibration, in every output string and comment.
n=12 does not support a calibration claim. Do not let the wording drift.

Tests:
- every surviving MCQ distractor has a Misconception edge
- an item with two defensible answers (fixture) is rejected by gate 3
- an item answerable without context (fixture) is rejected by gate 2
- rejection rate across the fixture chapter is > 0
- IRT fit is deterministic given a fixed seed

Done when `coursec build` prints items generated, rejections by gate, and the
difficulty/discrimination distribution.
---8<---

---

## D6 — Render and certify

---8<---
Read CLAUDE.md. This stage owns `emit` in §4 and reads everything else
read-only.

**Renderer** — `src/coursec/emit/`. Document IR → Typst → PDF. Four targets:
- course booklet
- cheat sheet — **pure template over the graph** (definitions, formulas, key
  facts). No LLM call in this path at all.
- question paper — greedy selection against a blueprint
  (`total_marks, sections, per-unit weightage, difficulty mix, Bloom mix,
  item-type mix`). If the blueprint is infeasible, report the binding constraint
  by name. Silent near-satisfaction is forbidden.
- answer key — worked solutions pulled from `ExecResult`, not regenerated

Render Mermaid to SVG at build time. Crop source figures by stored bbox.
Generate the bibliography by walking `evidenced_by`.

**Contract enforcement** — `src/coursec/emit/contract.py`. The 5 MVP slots from
CLAUDE.md I5. A Concept is emitted only with a status attached; `partial`
concepts list their unmet slots.

**Certificate** — `build/certificate.html`:
per-concept contract status, syllabus coverage %, unsupported-sentence count,
numeric-origin violations, item rejections by gate, quarantine count, and the
full diagnostic list.

**A build with any error-severity diagnostic produces no PDF at all.** Assert
this.

Tests:
- identical IR + fixed timestamp → byte-identical PDF
- the cheat-sheet path issues zero LLM calls (assert on the counter)
- an injected error diagnostic suppresses all PDF output
- an infeasible blueprint names its binding constraint

Done when `coursec build tests/fixtures/chapter.pdf` produces four PDFs and a
certificate, and you paste the certificate's headline numbers.
---8<---

---

## D7 — Quiz, mastery, personalized projection

---8<---
Read CLAUDE.md. This stage owns `learn` in §4.

**Quiz UI** — `src/coursec/app.py`, Streamlit. Serve generated items, capture
responses with the chosen option ID, not just correctness.

**Mastery** — `src/coursec/passes/mastery.py`. Per-concept posterior via
Bayesian Knowledge Tracing, using the D5 `(a, b)` as priors.

**Root-cause propagation** — walk `prerequisite_of` ancestors of each failed
concept; attribute the failure to the **deepest weak ancestor**, not the concept
the student visibly failed. Output names that concept explicitly.

**Misconception readout** — from the linked Misconception of the chosen
distractor. Report the faulty reasoning, not just "incorrect".

**Modality switch** — a student who failed after the formal slot is served the
intuition or worked-example slot next. Those slots already exist because of I5.

**Personalized PDF** — a filtered projection of the same graph through the same
D6 renderer. **No second code path.** Assert this by construction: the
personalized target must call the same render function with a subgraph.

Tests:
- **inject a synthetic student with a known prerequisite deficit; assert the
  system names that exact prerequisite**
- a student choosing a distractor gets that distractor's Misconception, not a
  generic one
- the personalized renderer and the course renderer are the same function
  (assert by identity, not by comparing output)

Done when a bad quiz run produces a named root cause, a misconception readout,
and a remediation PDF.
---8<---

---

## D8 — Demo run and failure injections

---8<---
Read CLAUDE.md.

Build the demo harness and the adversarial evidence for it.

**Adversarial fixtures** — `tests/fixtures/adversarial/`:
1. `chapter_wrong_constant.pdf` — one constant altered in the source
2. `item_two_answers.json` — an MCQ with two defensible answers
3. `contradicting_source.html` — a low-authority page asserting a wrong constant,
   served from a local fixture server so the demo is offline-safe

**Demo mode** — `coursec demo`: runs the full build with a streaming log, then
replays the three catches with the relevant certificate lines highlighted.

**RESULTS.md** — measured, pasted, not hoped for:
syllabus coverage %, contract-complete %, unsupported-sentence rate,
numeric-origin violations, item rejection rate by gate, quarantine count,
wall-clock, total token cost, and **second-run cache hit rate** (must be ~100%).

Also state plainly what did not work and which claims did not survive
measurement. A claim that did not survive is removed from the pitch, not
softened.

Tests:
- all three adversarial fixtures are caught, deterministically
- `coursec demo` runs end to end offline except for the retrieval stage
- second run of `coursec build` issues zero new LLM calls

Done when the full demo runs unattended and you paste RESULTS.md.
---8<---

---

## Order and dependencies

D0 → D1 → D2 → D3 → D4 → D5 → D6 → D7 → D8. Strictly sequential; each stage
reads the previous stage's tables.

If you fall behind schedule, the cut order is: D3 retrieval breadth (use two
tier-1 domains instead of nine), then D6's question-paper target, then D5's
synthetic pilot. **Never cut D4.** The verification and computation layer is the
entire argument of the project — without it this is a PDF exporter with a
language model attached.
