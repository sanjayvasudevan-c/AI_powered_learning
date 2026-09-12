# RUN_LOG.md

One row per stage. `make check` output is pasted in the PR/commit, not here —
this table is the index: what landed, when, and the commit it landed at.

| Stage | Date | Commit | Summary |
|---|---|---|---|
| D0 | 2026-09-11 | `ac1e0eb` | uv project scaffold, Diagnostic/DiagnosticSink, 3 CLI stubs, make check gate, CI. |
| D1 | 2026-09-11 | `8196bb0` | IR (12 SQLModel tables + Edge), Graph wrapper with ownership enforcement, heuristic PyMuPDF ingest pass, LLM call cache. `coursec build` now runs ingest for real. |
| D2 | 2026-09-11 | `ca2d3e2` | understand (LLM concept extraction + bge-small canonicalization), syllabus anchoring (hybrid retrieval, abstain), structure (fused-signal prerequisite DAG + cycle breaking, part_of edges), pyvis graph viz. `coursec build` now runs ingest -> understand -> structure. |
| D3 | 2026-09-11 | `dbdbce0` | gap (4-field GapVector, scale-invariant RetrievalBudget), evidence (real httpx fetch, robots.txt, per-domain rate limiting, BeautifulSoup nav-stripping + heading chunking, tiered scoring, admission policy, contradiction detection). `coursec build` now runs ingest -> understand -> structure -> gap -> evidence. |
| D4 | 2026-09-11 | `d868f7e` / `44139dc` | dossier (byte-stable grounding assembly), compose (per-slot generation, empty-dossier refusal enforced in code), verify (critic sees only cited spans, repair-then-drop, quarantine), compute (SymPy-in-subprocess), origin linter (I2). `coursec build` now runs the full pipeline through verify. |
| D5 | 2026-09-12 | `22461a8` + _pending push_ | items (per-Concept x Bloom-level generation, MCQ distractors linked to real Misconception nodes via evidenced-`assesses` edges), item_gates (4 blocking gates: key verification, leakage, single-answer, numeric execution), pilot (12-student synthetic SCREENING, 2PL MLE, quarantine). `coursec build` now runs the full pipeline through assess. |
