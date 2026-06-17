# progress.A.md — Lane A: Data & Retrieval

> Lane file. Owner: **Eng A — Data & Retrieval** (ingest, index, gates, retrieval).
> Global facts (build phase, contracts, decisions) live in the index `docs/progress.md` — don't duplicate them here.
> Headers below are stable so `/handoff` can target them. Append to the session log, newest first; rewrite the other sections from current state.

## In flight
- **`specs/a-001-ingest-sheets/` — spec written & signed off; no code yet.** Replaces the Slice-0 ingest stub with real **candidate** ingestion from `data/demand-supply.xlsx` (openpyxl, sheets-only). Branch `feat/a/001-ingest-sheets`. Public API: `ingest_candidates(path) -> tuple[dict[str, Candidate], IngestSummary]`. 5 tasks (T-001…T-005) ready to implement. Resolved decisions in the spec: OQ-1 new-joiner = `source=NEW_JOINER` (no `is_unverified` on frozen model); OQ-2 sheet-skill proficiency defaults to INTERMEDIATE; OQ-4 declare `openpyxl>=3.1`; OQ-5 return a 2-tuple, no `IngestResult` wrapper. **Open Roles mapping is OUT OF SCOPE** (may or may not be needed) — roles still come from the stub; `dsm/ingest/stub.py` is kept.

## Next up
1. Implement `a-001-ingest-sheets` T-001…T-005 (deps+contracts → parsers → candidate mapping → `ingest_candidates` orchestration+summary → docs/ADRs). Two ADRs to write during the work: OQ-2 (proficiency default) and OQ-4 (openpyxl dep). Lane C re-points the **candidate** source in `cli/commands.py` (cross-lane handoff; `cli/` not touched here).
2. Real index/retrieval over the vector store (Milvus) — Lane A per AD-062 (`ingest`/`index`/`modal`).
3. Docling profile enrichment (deferred to a later slice) — enriches candidates joined by email (AD-012/013).
   _(Note: gates were reassigned to Lane C by AD-062 and are already merged — not Lane A's anymore.)_

## Blockers / needs a human
- _(none)_

## Session log (append-only — newest first)
- **2026-06-17 · a-001-ingest-sheets spec** — Wrote `specs/a-001-ingest-sheets/{requirements,design,tasks}.md` for real candidate ingestion (openpyxl, sheets-only). Inspected the real workbook (4 tabs; data clean — no blanks/dupes, ISO date strings). Surfaced 5 conflicts with the frozen contract / tech.md and got sign-off: OQ-1 `source=NEW_JOINER`, OQ-2 INTERMEDIATE default, OQ-4 declare openpyxl, OQ-5 2-tuple return. Then **descoped Open Roles mapping** at the human's request (candidate-only; OQ-3 now moot). Spec only — no implementation. `make check` GREEN (66 tests, 2 import contracts).
- **2026-06-14 · lane-split** — Split shared `progress.md` into per-lane files; seeded this Lane A file from the Data & Retrieval slices. `make check` GREEN (29 tests, 2 import contracts). Next: Slice 1 real gates.
