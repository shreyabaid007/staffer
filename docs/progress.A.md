# progress.A.md — Lane A: Data & Retrieval

> Lane file. Owner: **Eng A — Data & Retrieval** (ingest, index, gates, retrieval).
> Global facts (build phase, contracts, decisions) live in the index `docs/progress.md` — don't duplicate them here.
> Headers below are stable so `/handoff` can target them. Append to the session log, newest first; rewrite the other sections from current state.

## In flight
- **`specs/a-001-ingest-sheets/` — implemented; `make check` GREEN (115 tests, 2 import contracts).** Real **candidate** ingestion replaces the Slice-0 candidate stub. New: `dsm/ingest/sheets.py` (public `ingest_candidates(path) -> tuple[dict[str, Candidate], IngestSummary]`, openpyxl `data_only=True`, sheets-only) + `dsm/ingest/models.py` (`RowIssue`, `IngestSummary`, `IngestError`). Branch `feat/a/001-ingest-sheets`; T-001…T-005 all committed (one commit each). Reads the three supply tabs (Beach→`FreeNow`, Rolling Off→`RollingOff`, New Joiners→`NewJoiner`); `Open Roles` tab ignored. Real-file smoke asserts **35 candidates** (10/10/15), `issues==[]`. Resolved decisions landed: OQ-1 `source=NEW_JOINER`; OQ-2 → **AD-066** (INTERMEDIATE default); OQ-4 → **AD-065** (`openpyxl>=3.1` declared); OQ-5 2-tuple, no `IngestResult`. **Open Roles mapping stays OUT OF SCOPE** — roles still come from the stub; `dsm/ingest/stub.py` `get_stub_role` untouched. Not yet merged to `main`.

## Next up
1. **Cross-lane handoff (Lane C):** re-point the **candidate** source in `dsm/cli/commands.py` from `get_stub_candidates` to `ingest_candidates(...)` (returns a 2-tuple now; use `candidates.values()` and surface/ignore the `IngestSummary`). `cli/` was deliberately **not** touched in this feature. The roles stub (`get_stub_role`) stays until Open Roles ingest is scoped.
2. Real index/retrieval over the vector store (Milvus) — Lane A per AD-062 (`ingest`/`index`/`modal`).
3. Docling profile enrichment (deferred to a later slice) — enriches candidates joined by email (AD-012/013); will overwrite the placeholder INTERMEDIATE proficiency (AD-066).
   _(Note: gates were reassigned to Lane C by AD-062 and are already merged — not Lane A's anymore.)_

## Blockers / needs a human
- _(none)_

## Session log (append-only — newest first)
- **2026-06-17 · a-001-ingest-sheets implementation** — Implemented T-001…T-005 (one commit each): added `openpyxl>=3.1` + `dsm/ingest/models.py` (`RowIssue`/`IngestSummary`/`IngestError`); pure parsers (`parse_date`/`parse_skills`/`parse_location`/`_header_index`/`_is_blank`); per-tab `_row_to_candidate` + availability mapping; `ingest_candidates` orchestration (blank-skip, duplicate first-wins, validation→`RowIssue`, fixed Beach→Rolling Off→New Joiners order, non-supply tabs ignored). Tests are mock-free synthetic workbooks (`openpyxl.Workbook()`) + a real-file smoke (35 candidates, 10/10/15, `issues==[]`). Wrote **AD-065** (openpyxl dep) and **AD-066** (INTERMEDIATE proficiency default); noted the summary return in `docs/structure.md`. Roles stub untouched. `make check` GREEN — 115 tests, 0 type errors, 2 import contracts. Next: Lane C re-points the candidate source in `cli/commands.py`.
- **2026-06-17 · a-001-ingest-sheets spec** — Wrote `specs/a-001-ingest-sheets/{requirements,design,tasks}.md` for real candidate ingestion (openpyxl, sheets-only). Inspected the real workbook (4 tabs; data clean — no blanks/dupes, ISO date strings). Surfaced 5 conflicts with the frozen contract / tech.md and got sign-off: OQ-1 `source=NEW_JOINER`, OQ-2 INTERMEDIATE default, OQ-4 declare openpyxl, OQ-5 2-tuple return. Then **descoped Open Roles mapping** at the human's request (candidate-only; OQ-3 now moot). Spec only — no implementation. `make check` GREEN (66 tests, 2 import contracts).
- **2026-06-14 · lane-split** — Split shared `progress.md` into per-lane files; seeded this Lane A file from the Data & Retrieval slices. `make check` GREEN (29 tests, 2 import contracts). Next: Slice 1 real gates.
