# CLAUDE.md

Standing rules for this repository. Read fully before any stage. Binding.

---

## 1. What this is

**CourseC is a course compiler.** Source material is the input language, the
Concept Graph is the IR, each stage is a typed pass, and the PDF / cheat sheet /
question paper / remediation path are targets.

This is an **MVP demo**, not a deployable product. Scope decisions live in
`DEMO_PLAN.md` §1. Do not re-add cut scope. If a stage seems to need cut scope,
halt and say so rather than quietly building it.

If a proposed change makes the system less like a compiler and more like a
prompt chain, reject it — even if it demos well.

---

## 2. Invariants

A violation is a build failure, never a warning, never a TODO.

**I1 — No unsupported sentence.** Every emitted factual sentence carries ≥1
`evidenced_by` edge and passes entailment against the union of its cited spans.
Unentailed → repair (max 2 attempts) → drop → quarantine. Never emitted unmarked.

**I2 — Numbers are computed, never generated.** Every numeric literal in emitted
material originates from a `SourceSpan`, a cited `WebEvidence` span, or a sandbox
execution result. A number with no origin is an error diagnostic. The language
model phrases explanations; it does not produce values.

**I3 — Provenance is total.** Every node reachable from an emitted document
traces to `(file|url, locator, retrieved_at, content_hash)`. A broken chain
fails the build.

**I4 — The prerequisite graph is acyclic.** Cycles are broken by a deterministic
documented policy. Every break is logged as a diagnostic.

**I5 — Contract before emission.** No concept enters a target document without a
contract status. MVP contract = 5 slots: definition, intuition, worked example,
visual-or-analogy, ≥2 items. Unmet slots are enumerated in the certificate.

**I6 — Contradiction is surfaced, never resolved.** When vetted evidence
contradicts the source, emit the source as primary and the divergence as a
marked note with both citations. Overwriting the teacher's material is forbidden.

**I7 — Licence-clean media only.** Images come from a bbox crop of the uploaded
source, from generated diagram code, or from a verified open licence. Nothing
else is emitted.

---

## 3. Quarantine

Any artifact failing its gate after `max_attempts` moves to `build/quarantine/`
with its failing diagnostic, is excluded from all targets, and is counted on the
certificate.

Quarantine is the autonomous substitute for human review. Never emit a failing
artifact "with a caveat". Never lower a threshold so something passes.

---

## 4. Pass ownership

A pass writes only its own column. Writing outside it is a defect, not a
refactor opportunity.

| Pass | Writes | Reads |
|---|---|---|
| ingest | `Block`, `SourceSpan` | files |
| understand | `Concept`, `Alias`, `SyllabusNode` | `Block` |
| structure | `prerequisite_of`, `part_of` | `Concept` |
| gap | `GapVector`, `RetrievalBudget` | `Concept`, `SyllabusNode` |
| evidence | `WebEvidence`, `evidenced_by`, `contradicts` | `GapVector` |
| compose | `LessonBlock` | dossiers |
| verify | `Verdict`, `ExecResult` | `LessonBlock` |
| assess | `Item`, `Misconception`, `ItemStats` | `Concept`, `LessonBlock` |
| emit | `Document`, `Certificate` | everything, read-only |
| learn | `Mastery`, `Plan` | `Item`, responses |

---

## 5. Git and autonomy

**On `make check` green:** stage-scoped `git add`, conventional commit, push,
print the SHA.

**On red: HALT.** State exactly what failed. Do not commit. Do not weaken a
test, relax a threshold, mark a test `xfail`, or use `--no-verify`.

**On spec ambiguity: HALT and ask.** List the options and the tradeoff. Silent
assumptions are the highest-cost failure mode in this project.

Never force-push. Never commit `build/`, `data/`, `.env`, `.cache/`, weights, or
any secret. Commit format:

```
D<n>: <imperative summary>

<what changed, what is now provable>
```

---

## 6. Measurement discipline

- Metrics are **measured and pasted**, never asserted. "Should be around 90%" is
  not a measurement.
- Paste real command output. Never summarise output in place of showing it.
- Specification docs are kept consistent with measured reality. A discrepancy is
  corrected at the source and recorded as a specification error, not papered
  over.
- Real defects surface through **assertion-level tests** — uniqueness,
  conservation, binding, and origin assertions — not through metrics the
  pipeline computes about itself.

---

## 7. Tests

`make check` = `ruff check` + `pytest -q`. Coverage target applies to
`src/coursec/core/` only. Do not gold-plate a demo.

Four kinds, each stage writes what applies:

1. **Unit** — pure functions, schema validation.
2. **Property** — invariants under Hypothesis: DAG acyclicity, provenance
   totality, idempotent recompile, contract monotonicity.
3. **Golden** — fixed fixture chapters with committed IR snapshots. Diffs are
   reviewed, never blanket-regenerated.
4. **Adversarial** — poisoned inputs the system must catch: a wrong constant in
   the source, a low-authority page contradicting the textbook, an MCQ with two
   defensible answers.

**If the adversarial suite passes on first write, it is too weak.** Strengthen it
before proceeding.

---

## 8. Cost rules

- Cheap model: extraction, classification, linking, entailment screening.
- Strong model: synthesis, item authoring, critique.
- Every LLM call is keyed by `sha256(model, prompt, params)` in a persistent
  on-disk cache.
- **A rebuild with unchanged inputs must issue zero new LLM calls.** This is a
  tested property with an assertion on the call counter, not an aspiration.
- Dossier prefixes must be byte-stable across runs. Churning prefixes destroy
  the cache.
- Never call an LLM inside a loop over all pairs. Candidate-filter first.

---

## 9. Environment

Windows dev host, no local GPU, Python via `uv`. Nothing in the MVP requires a
GPU. Stack: SQLite + SQLModel, `typer` CLI, Streamlit UI, Typst for rendering,
PyMuPDF for ingest, local `bge-small` embeddings.

Generated code is untrusted input. Execute it in a subprocess with a timeout and
no network. This sandbox is demo-grade and that limitation is documented, not
hidden.

---

## 10. Style

- Type hints on every public function. `Optional` over bare `None` defaults.
- Pydantic/SQLModel for every boundary. No untyped dicts crossing a pass.
- No bare `except`. No silent `pass` in an exception handler.
- Diagnostics are structured objects with severity, not log strings.
- Prefer deletion over deprecation. This repo has no users yet.

---

## 11. Decision log

Append only. Corrections are new rows citing the old.

| # | Date | Decision | Rationale |
|---|---|---|---|
| 1 | — | Compiler framing over pipeline framing | Determinism, incremental rebuild, diagnostics, autonomy |
| 2 | — | Quarantine replaces human review | Autonomy needs a substitute for the reviewer, not its deletion |
| 3 | — | Numbers never produced by an LLM | Verified computation is the accuracy floor |
| 4 | — | Trained models cut from MVP | They buy cost and latency, which a demo does not judge |
| 5 | — | SQLite over Postgres | One dependency, adequate at chapter scale |
| 6 | D1 | §4's ownership table named this node type `SyllabusRef`; corrected to `SyllabusNode` | D1/D2 of PROMPTS.md — the text that actually defines the node's shape — names it `SyllabusNode` throughout. §6: a discrepancy is corrected at the source, not papered over |
| 7 | D2 | `.gitignore` excludes `data/` (§5) but D2 requires committing a hand-authored `data/syllabus.yaml` | `data/` is for runtime state (retrieved evidence, downloaded weights); hand-authored curriculum content is source, not a build artifact. Allow-listed `data/*.yaml` rather than moving the file, since PROMPTS.md's path is explicit |
| 8 | D2 | `part_of` edges are created by `structure`, not `understand`, despite D2's Extraction section mentioning them | §4's ownership table assigns `part_of` to `structure`; `Graph.add` enforces this, so `understand` could not create one even if asked. `understand` records section membership as data; `structure` turns it into edges |
