# a-001-ingest-sheets — Tasks

> Ordered, atomic, independently testable. **One task = one commit**, imperative, referencing
> the spec (e.g. `feat(ingest): map supply rows to Candidate per I-CAND-1..6`).
> Decisions OQ-1…OQ-5 **signed off 2026-06-16** (see requirements §Resolved decisions) — ready
> to implement. `make check` must be green at the end of every task.

- [ ] **T-001 — Declare openpyxl + add ingest module-local contracts.**
  Add `openpyxl>=3.1` to `pyproject.toml` dependencies and record the ADR (OQ-4). Create
  `dsm/ingest/models.py` with `RowIssue`, `IngestSummary` (both `frozen=True`) and
  `IngestError`. **No `IngestResult` wrapper** (OQ-5). Add a model instantiation test.
  *Verifies:* I-SUM-1 (shape), OQ-4/OQ-5. *Commit:* `feat(ingest): add ingest contracts + openpyxl dep (AD-0xx)`.

- [ ] **T-002 — Pure parsing helpers + tests.**
  Implement `parse_date`, `parse_skills` (INTERMEDIATE default per OQ-2), `parse_location`,
  `_header_index`, `_is_blank` in `dsm/ingest/sheets.py`. Add `tests/ingest/test_parsing.py`
  covering the cases in design §Eval (ISO/`date`/`datetime`, bad dates, skill normalise +
  de-dupe, all four location variants, missing-header → `IngestError`).
  *Verifies:* I-LOAD-3, I-CAND-3, I-CAND-4, I-EDGE-3. *Commit:* `feat(ingest): pure cell/date/location/skill parsers per I-CAND-3..4`.

- [ ] **T-003 — Candidate row mapping (3 supply tabs).**
  Implement `_row_to_candidate` and per-tab availability mapping (Beach→`FreeNow`,
  Rolling Off→`RollingOff`, New Joiners→`NewJoiner`); set `source` correctly (OQ-1
  representation). Empty `feedback`. Unit-test each tab incl. confidence carry-through and the
  new-joiner `source=NEW_JOINER` assertion.
  *Verifies:* I-CAND-1, I-CAND-2, I-CAND-5, I-CAND-6. *Commit:* `feat(ingest): map supply rows to Candidate per I-CAND-1..6`.

- [ ] **T-004 — Open Role row mapping (raw skills preserved).**
  Implement `_row_to_role`: `required_skills=[]`, raw *Required Skills* + *Notes* folded into
  `description` (OQ-3), `co_location_required` from *Co-location*, `start_date` from *Start*,
  `location` via `parse_location(..., None)`. Unit-test that skills are NOT parsed and the raw
  text survives verbatim.
  *Verifies:* I-ROLE-1, I-ROLE-2. *Commit:* `feat(ingest): map Open Roles rows to OpenRole, raw skills per I-ROLE-2`.

- [ ] **T-005 — `ingest_workbook` orchestration + summary + edge cases.**
  Tie the helpers together: open the workbook (`data_only=True`), iterate the 4 tabs in fixed
  order, apply blank-skip, duplicate-email first-wins, and validation-failure→`RowIssue`
  handling; accumulate counts; return `(candidates, roles, summary)`. Add `tests/ingest/test_sheets.py`
  (synthetic workbooks for blank/duplicate/bad-date/bad-confidence/missing-tab + determinism)
  and the real-file smoke test with asserted counts.
  *Verifies:* I-LOAD-1/2, I-VAL-1, I-SUM-1, I-EDGE-1/2/3, I-DET-1. *Commit:* `feat(ingest): assemble ingest_workbook + summary per I-SUM-1, I-EDGE-1..3`.

- [ ] **T-006 — Docs, handoff & stub-retirement note.**
  Append the accepted ADR(s) to `docs/decision.md` (OQ-2 proficiency default, OQ-4 openpyxl).
  Add a one-line note to `docs/structure.md` line 42 that ingest also returns the typed
  `IngestSummary` as a third element (contract still `dict + list`). Record in
  `docs/progress.A.md` (via
  `/handoff`) that `dsm/ingest/stub.py` is superseded and **Lane C must re-point
  `dsm/cli/commands.py`** from `get_stub_candidates`/`get_stub_role` to `ingest_workbook`
  (cross-lane handoff; `cli/` is not touched here). Do **not** edit `docs/progress.md` (index
  is refreshed only at merge).
  *Verifies:* Definition of Done. *Commit:* `docs(ingest): record ADRs + Lane C handoff for ingest_workbook`.

## Mapping to acceptance criteria
| Task  | Criteria |
|-------|----------|
| T-001 | I-SUM-1 (shape), OQ-4, OQ-5 |
| T-002 | I-LOAD-3, I-CAND-3, I-CAND-4, I-EDGE-3 |
| T-003 | I-CAND-1, I-CAND-2, I-CAND-5, I-CAND-6 |
| T-004 | I-ROLE-1, I-ROLE-2 |
| T-005 | I-LOAD-1, I-LOAD-2, I-VAL-1, I-SUM-1, I-EDGE-1, I-EDGE-2, I-EDGE-3, I-DET-1 |
| T-006 | record-keeping / DoD |

## Out of scope (this spec)
Profile/feedback enrichment · vector index · `cli/` wiring · days-on-beach logic (AD-050).
