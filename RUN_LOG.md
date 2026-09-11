# RUN_LOG.md

One row per stage. `make check` output is pasted in the PR/commit, not here —
this table is the index: what landed, when, and the commit it landed at.

| Stage | Date | Commit | Summary |
|---|---|---|---|
| D0 | 2026-09-11 | `ac1e0eb` | uv project scaffold, Diagnostic/DiagnosticSink, 3 CLI stubs, make check gate, CI. |
| D1 | 2026-09-11 | `8196bb0` | IR (12 SQLModel tables + Edge), Graph wrapper with ownership enforcement, heuristic PyMuPDF ingest pass, LLM call cache. `coursec build` now runs ingest for real. |
| D2 | 2026-09-11 | _pending push_ | understand (LLM concept extraction + bge-small canonicalization), syllabus anchoring (hybrid retrieval, abstain), structure (fused-signal prerequisite DAG + cycle breaking, part_of edges), pyvis graph viz. `coursec build` now runs ingest -> understand -> structure. |
