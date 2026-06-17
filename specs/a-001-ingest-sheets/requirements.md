# a-001-ingest-sheets — Requirements

> Lane A (Data & Retrieval). Replace the Slice-0 ingest stub (`dsm/ingest/stub.py`) with
> real ingestion of the **candidate supply sheets** in `data/demand-supply.xlsx` using
> openpyxl. **Sheets-only** — Docling profile enrichment is deferred to a later slice (per
> `docs/progress.A.md` Next-up #2). **Candidate-only** — Open Roles mapping is **out of
> scope** for this feature (it may or may not be needed; not decided here).

## User story
As the staffing engine, I need to load the current consultant supply snapshot from the Excel
workbook into typed `Candidate` records, so the gate → retrieve → score → rank pipeline runs
over real people instead of hardcoded stubs — and so any row that can't be trusted is
**reported, never silently dropped**.

## Product invariants referenced
- **Candidate universe = the three supply sheets** (Beach / Rolling Off / New Joiners),
  joined by **email** (`docs/product.md` §rules; AD-012, AD-013).
- **Trade-offs surfaced, never hidden** — new joiners' skills are counted but their
  uncertainty is preserved for downstream flagging (`docs/product.md`; AD-032).
- **Errors are explicit** — "parse/extraction failures are logged and reported, never
  silently swallowed" (`docs/tech.md` §Coding standards).

## Scope
- **In:** read the three supply tabs (`Beach`, `Rolling Off`, `New Joiners`) of
  `data/demand-supply.xlsx`; map supply rows → `Candidate`; produce a typed ingest summary of
  what was loaded and what failed validation; handle duplicate emails, blank rows,
  unparseable dates.
- **Out:** **Open Roles mapping → `OpenRole` (out of scope — not part of this feature)**; profile
  (PDF) enrichment, feedback parsing, the vector index, any CLI wiring (Lane C owns `cli/`;
  this spec exposes the function it will call). `Candidate.feedback` is left empty
  (`FeedbackSignals()`); `profile_summary` / `years_experience` stay `None`.

## Acceptance criteria (EARS)

### Loading
- **I-LOAD-1** — WHEN `ingest_candidates(path)` is called with a readable `.xlsx`, the system
  SHALL open it with openpyxl in `data_only=True` mode and locate the tabs `Beach`,
  `Rolling Off`, `New Joiners` by sheet name. (Any other tab, e.g. `Open Roles`, is ignored.)
- **I-LOAD-2** — WHEN an expected supply tab is absent or its header row is missing required
  columns, the system SHALL raise a typed `IngestError` (a structural failure is fatal —
  it is not a per-row issue) naming the offending tab/column.
- **I-LOAD-3** — Each tab's **title row is row 1**, the **header row is row 2**, and **data
  begins at row 3**. The system SHALL resolve columns by header name (not fixed position).

### Candidate mapping (Beach / Rolling Off / New Joiners)
- **I-CAND-1** — WHEN a supply row is valid, the system SHALL emit one `Candidate` with
  `email` (join key, AD-012), `name`, `location`, `availability`, `skills`, an empty
  `feedback`, and `source` set to the tab's `CandidateSource` (`beach` / `rolling_off` /
  `new_joiner`).
- **I-CAND-2 (availability = sheet membership)** — Beach rows SHALL get `FreeNow`; Rolling
  Off rows SHALL get `RollingOff(expected_date, confidence)` from the *Roll-off Date* and
  *Confidence* columns; New Joiners rows SHALL get `NewJoiner(join_date)` from *Join Date*.
- **I-CAND-3 (skills)** — *Key Skills* (and *Key Skills (from CV)*) SHALL be split on commas,
  trimmed, lowercased, and de-duplicated into `Skill` records. Proficiency is **not present
  in the sheets**; see **OQ-2** for the default this requirement depends on.
- **I-CAND-4 (location + Chennai-open)** — The *Location* column maps to `Location.city`;
  `Location.remote_eligible` SHALL be `True` when *Chennai-open* is `Yes` **or** the
  location text denotes remote (e.g. `Remote (India)`), else `False` (AD-020, AD-063a).
  `country` defaults to `"India"`.
- **I-CAND-5 (roll-off confidence)** — The *Confidence* value SHALL be carried verbatim onto
  `RollingOff.confidence` (`high`/`medium`/`low`); a value outside that set is a validation
  failure (I-VAL-1). It is recorded as data only — never used to gate (AD-022).
- **I-CAND-6 (new-joiner uncertainty)** — New Joiners rows SHALL set
  `source=CandidateSource.NEW_JOINER`. The frozen `Candidate` model has **no `is_unverified`
  field**; new-joiner-ness is represented by `source`, from which the downstream
  `unverified_skills` flag is derived (AD-032). See **OQ-1**.

### Validation, summary & edge cases
- **I-VAL-1** — WHEN a row fails validation (missing email/name, unparseable or missing
  required date, bad confidence, or any `Candidate` `ValidationError`), the system SHALL skip
  that row, NOT raise, and record a `RowIssue{sheet, row_number, email, reason}`.
- **I-SUM-1** — The system SHALL return an `IngestSummary` with counts (candidate rows seen,
  candidates ingested, blank rows skipped, duplicate emails skipped) and the full list of
  `RowIssue`s. **No failure is silent** (`docs/tech.md`).
- **I-EDGE-1 (duplicate emails)** — WHEN two supply rows share an email (within or across
  tabs), the **first occurrence wins** (tab order Beach → Rolling Off → New Joiners, then row
  order); each later duplicate SHALL be skipped, counted in `duplicate_emails_skipped`, and
  recorded as a `RowIssue`. Result candidates are keyed by email (AD-012).
- **I-EDGE-2 (blank rows)** — WHEN every cell in a data row is empty/`None`, the system SHALL
  skip it as a blank row (counted in `blank_rows_skipped`), NOT as a validation failure.
- **I-EDGE-3 (unparseable dates)** — Date cells SHALL be accepted as a `date`, a `datetime`,
  or an ISO `YYYY-MM-DD` string; anything else is a validation failure (I-VAL-1).

### Determinism
- **I-DET-1** — Same workbook → same `(candidates, summary)` every run: stable ordering (sheet
  order, then row order), deterministic de-duplication, no wall-clock or randomness (AD-001;
  `docs/tech.md` §Determinism).

## Resolved decisions (signed off 2026-06-16)
These were conflicts/gaps between the task brief and the **frozen** contract (AD-060) or
`docs/tech.md`. Resolved with the human before code; OQ-2 and OQ-4 each become a new ADR.

- **OQ-1 — `is_unverified` has no home on the frozen model.** ✅ **Use `source=NEW_JOINER`.**
  `Candidate` (frozen, AD-060) has no `is_unverified` field and `NewJoiner` carries only
  `join_date`, so new-joiner-ness is represented by `source`; the `unverified_skills` flag is
  applied downstream by `match/score` per AD-032. No contract change.
- **OQ-2 — proficiency default for sheet skills.** ✅ **Default `ProficiencyLevel.INTERMEDIATE`.**
  The supply sheets carry no proficiency and `Skill.proficiency` is required; every
  sheet-sourced skill defaults to INTERMEDIATE, to be overwritten when profiles (later slice)
  supply real levels. → **new ADR.** (Grade-based inference rejected: grade is seniority, not
  per-skill proficiency.)
- **OQ-3 — Required Skills parsing.** ⛔ **Moot — Open Roles mapping is out of scope** for this
  candidate-only feature. (If Open Roles ingest is ever taken on, revisit then whether ingest
  parses skills or leaves them for `clarify`.)
- **OQ-4 — openpyxl dependency.** ✅ **Declare `openpyxl>=3.1` explicitly in `pyproject.toml`.**
  It is in `uv.lock` only transitively (via docling); relying on that is fragile. → **new ADR**
  (`docs/tech.md`: no new deps without an ADR).
- **OQ-5 — return contract shape.** ✅ **Return `tuple[dict[str, Candidate], IngestSummary]`** —
  candidates keyed by email (AD-012) plus the typed summary; **no `IngestResult` wrapper**
  (declined). `docs/structure.md` line 42 lists the ingest phase as producing
  `dict[email, Candidate]` (and `list[OpenRole]`, which this feature does not build); a
  one-line note records that candidate ingest also returns the summary.
