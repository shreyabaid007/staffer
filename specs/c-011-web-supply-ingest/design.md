# c-011 — Web supply ingest (design)

Status: implemented with this slice (spec = design record, per the c-008/c-009 precedent).

## Modules touched
| Module | Change |
|---|---|
| `dsm/ingest/enrich_cache.py` | **new** — `FileEnrichCache`: content-keyed extraction cache (FR-5-AC-2) |
| `dsm/ingest/blobstore.py` | add `read_records(source_hash, bronze_dir)` (FR-5-AC-1) |
| `dsm/cli/commands.py::ingest` | full-corpus normalize (LANDED+SKIPPED via persisted bronze), enrich-cache wiring, gold write gate (FR-5) |
| `dsm/web/models.py` | supply/ingest DTOs (web-local; frozen contract untouched) |
| `dsm/web/service.py` | supply read/mutate, attachment writes, job runner |
| `dsm/web/app.py` | new routes `/supply*`, `/ingest/*` |
| `dsm/web/static/index.html` | two-tab page; supply table per approved mockup |
| `dsm/cli/commands.py` | tiny `derive_candidate_id(email)` helper so `dsm.web` never imports `dsm.pii` (NF-1) |

## A. Ingest incrementality fix (FR-5)

### A.1 Full-corpus merge
The parse/normalize loop currently `continue`s on anything not `LANDED`. Change: process
entries whose status is `LANDED` **or** `SKIPPED` (both have `raw_bytes_hash` + `source_type`;
`INVALID` still skipped). For `SKIPPED` entries, load bronze records via new
`read_records(source_hash, bronze_dir)` (the `records/<hex>.jsonl` written when the blob first
landed) — no Docling re-parse; fall back to `parse_blob` when the records file is missing
(bronze wiped but manifest intact). Normalization stays deterministic + cheap.

Consequences, all intended:
- `all_normalized` = the whole current corpus → `merge_run` output preserves enrichment for
  unchanged candidates (G-1 fixed) — **provided** their extractions are available, which the
  cache guarantees without LLM calls (A.2).
- `bronze_supply` (identities / `known_pii`) now covers **all** supply sheets each run — fixes
  a latent weaker-redaction path on partial runs (previously only newly-landed sheets fed
  `known_pii`).
- The AD-093 roster re-parse loop becomes redundant (`current_ids == landed_ids`) but is
  **kept** as a cheap invariant guard (defence in depth against a future regression of this
  loop's semantics).
- `land()` already returns SKIPPED entries with `raw_bytes_hash`, `source_type`,
  `snapshot_date` — verified before implementation; if `snapshot_date` turns out to be
  None-on-skip, re-derive it the same way `land` does (it is parsed from content, which we
  have by hash).

### A.2 Enrich cache — `FileEnrichCache`
Mirrors the c-006 `FileIntakeCache` precedent (AD-066: content-hash + pinned
`(model, prompt_version)` derivation).

- Key: `sha256(candidate_id · source_hash · row_index · prompt_version · model_version)`.
- Value file: `data/enrich_cache/<key>.json` — `{"kind": "resume"|"feedback", "payload": <extraction model dump>}`,
  written atomically (temp+rename). Location: sibling of gold (`gold_dir.parent /
  "enrich_cache"`), gitignored like all of `data/`.
- Wiring (composition root only — `dsm/ingest/enrich.py` itself stays cache-free):
  `guard → cache.get → (hit: reuse) | (miss: enrich_* → cache.put)`. The injection guard runs
  before the cache so a newly-tightened guard still screens cached-era content cheaply.
- A **failed/None** extraction is *not* cached (retry next run). A leak-scan trip still
  aborts the run (unchanged).
- Invalidation = key change (`prompt_version`/`model_version` bump, changed bytes → new
  `source_hash`). No TTL. Cache is derived data: deleting the directory is always safe.

### A.3 Gold write gate
Before `write_gold(g)`: `prior = read_gold(g.candidate_id)`; skip when `prior is not None and
prior.gold_hash == g.gold_hash and prior.is_tombstoned == g.is_tombstoned`. Count
`updated` vs `unchanged` in the `── Gold ──` summary. Tombstone/revive writes unchanged.
The early-return "pure idempotent re-run" branch is superseded by this gate (gold list is now
always non-empty when supply exists) — replaced by the summary line `gold unchanged: N`.

### A.4 What stays expensive (recorded, not fixed here)
Editing a supply CSV changes that blob's hash → provenance (`source_hash`) inside the sheet's
candidates' gold → their `gold_hash` changes → the **edited sheet's** candidates re-embed
(one batched Modal call; cents). Bounded and correct: only the touched sheet, never the whole
pool, and never the LLM-enrich path. A provenance-excluding content hash would need a merge
redesign — out of scope (backlog).

## B. Web API

### B.1 DTOs (`dsm/web/models.py`, all web-local)
```python
class SupplyRowView(BaseModel):
    candidate_id: str          # pseudonym (HMAC) — stable handle for status joins
    name: str; email: str
    grade: str | None; skills: list[str]; location: str | None
    chennai_open: bool | None
    category: Literal["beach", "rolling_off", "new_joiner"]
    roll_off_date: str | None = None; confidence: str | None = None   # rolling_off
    join_date: str | None = None                                      # new_joiner
    days_on_beach: int | None = None; notes: str | None = None
    ingested: bool; has_resume: bool; feedback_count: int
    feedback_files: list[str] = []      # web-written + raw feedback filenames linked by email

class SupplySheetView(BaseModel):
    category: ...; as_of: str | None; rows: list[SupplyRowView]; skipped: list[str]

class SupplyResponse(BaseModel):
    sheets: list[SupplySheetView]

class AddCandidateRequest(BaseModel):
    category: ...; name: str; email: EmailStr; grade: str | None = None
    skills: list[str] = []; location: str | None = None; chennai_open: bool = False
    roll_off_date: date | None = None; confidence: Literal["high","medium","low"] | None = None
    join_date: date | None = None; notes: str | None = None
    # model_validator: category-specific required fields (FR-2-AC-4)

class FeedbackWriteRequest(BaseModel):
    text: str                     # markdown
    source: Literal["internal_ee", "client"] = "internal_ee"

class IngestRunResponse(BaseModel):  job_id: str; state: str
class IngestStatusResponse(BaseModel):
    state: Literal["idle","running","succeeded","failed"]
    job_id: str | None; started_at: str | None; finished_at: str | None
    summary: IngestSummaryView | None    # parsed counts, below
    log_tail: list[str] = []             # last N lines on failure

class IngestSummaryView(BaseModel):      # parsed from captured CLI output (PII-safe lines only)
    landed: int; skipped: int
    gold_updated: int; gold_unchanged: int; tombstoned: int; revived: int
    indexed: int; index_skipped_unchanged: int; index_removed: int; enrich_llm_calls: int
```

### B.2 Routes (`dsm/web/app.py`)
- `GET  /supply` → `SupplyResponse`
- `POST /supply/candidates` (`AddCandidateRequest`) → row view; 409 duplicate email
- `DELETE /supply/candidates/{category}/{email}` → 204; 404 unknown
- `POST /supply/candidates/{email}/resume` (multipart pdf) → `{stored, link_check}`
- `DELETE /supply/candidates/{email}/resume` → 204
- `POST /supply/candidates/{email}/feedback` (json `FeedbackWriteRequest` **or** multipart file) → `{stored}`
- `DELETE /supply/candidates/{email}/feedback/{filename}` → 204 (traversal-guarded)
- `POST /ingest/run` → 202 `IngestRunResponse`; 409 while running
- `GET  /ingest/status` → `IngestStatusResponse`

### B.3 Service mechanics (`dsm/web/service.py`)
- **CSV I/O:** raw `csv` module on `data/raw/supply/<Sheet>.csv` preserving the file's own
  header row verbatim; append maps request fields onto the sheet's columns (`Email`, `Name`,
  `Grade`, `Key Skills*`, `Location`, `Chennai-open`, per-sheet date/confidence columns);
  unknown columns get "". Banner line 1 rewritten with `as of <today>`. Atomic temp+rename.
  Row identity for delete = `Email` column (case-insensitive strip).
- **Status join:** `commands.derive_candidate_id(email)` (new thin helper; keeps
  `dsm.web ⊥ dsm.pii`) → `read_gold(cid)` for `ingested`/`feedback_count`;
  `_silver_resume_hashes` (existing c-008 helper) for `has_resume`.
- **Resume filename:** `<email local part, sanitised>.pdf`. `link_check`: reuse
  `dsm.ingest.parse.pdf` extraction on the uploaded bytes and search for the email; on
  extraction error report `no_email_found` (warn-only — ingest will OCR-fallback).
- **Feedback filename:** `<email local part>-web-<utc timestamp>.md`; content =
  `email: <email>` line + `## {Client|Project} feedback — web` heading ensured, then the
  payload verbatim. Uploaded files: same ensure-link pass.
- **Job runner:** module-level single-flight (`threading.Lock` + job dict). Runs in a
  `threading.Thread`: `subprocess.run([sys.executable, "-m", "dsm", "ingest"], ...)` then
  `... "index"`, cwd=repo root, env inherited (requires `DSM_CANDIDATE_ID_KEY`, as `dsm serve`
  already documents), stdout+stderr captured to `data/ingest_jobs/<job_id>.log`. Summary
  parsed from the PII-safe summary lines (`landed :`, `entities`, `indexed`, …). Non-zero exit
  → `failed` + log tail (lines are PII-safe by the pipeline's own output discipline).
- Subprocess (not in-process import) because the CLI body owns typer echo/exit semantics and
  the OMP/KMP workaround (AD-108), and it isolates faiss/docling memory from the serving
  process. Same-machine single Milvus writer caveat → NF-4.

## C. Frontend (`static/index.html`)
One page, two primary tabs (FR-6). Tab 2 = existing match UI **unchanged** (same ids/handlers).
Tab 1 implements the approved mockup: grouped one-table view (three `tbody` sections), inline
ghost→edit add row, per-row Resume cell (upload btn ⇄ file chip + ✕) and Feedback cell
(💬 count chip + ✕, ✎ write → Markdown editor expansion row with source select, ⬆ file),
row ✕ remove, filter box, `Run ingest` button + status pill polling `GET /ingest/status`
every 2s while running, then rendering the `IngestSummaryView` as a one-line result
("2 updated · 9 unchanged · 1 tombstoned · 1 indexed · 9 skipped"). Status chips per row:
`synced` (ingested) / `pending ingest`. All dynamic strings escaped; handlers delegated via
`data-*` (c-008 XSS rule).

## Edge cases
- Add then delete before ingest → row never reaches gold; delete of a not-yet-ingested row is
  just a CSV edit (no tombstone — reconcile never saw it).
- Re-add of a previously removed candidate → revive path (AD-094) — covered by existing tests,
  re-asserted in the two-run web test.
- Resume uploaded for an email not in any sheet → allowed (file lands, links when the row
  arrives); UI surfaces it only via the row's `has_resume` once both exist.
- Feedback for a candidate in **no** sheet is parsed but merges to a candidate_id with no
  supply record → `merge_candidate` returns a gold entity only for silver-supply candidates
  (verified: candidates enter via supply, AD-013) — harmless orphan; not indexed.
- Banner date update makes freshness `valid_as_of` = today → never trips staleness refuse
  after web edits (`reconcile.max_staleness_days`).
- Two web replicas are out of scope (single `dsm serve`, NF-4 / Milvus Lite note).

## Eval / test cases to add
1. **tests/ingest/test_enrich_cache.py** — key derivation (version bumps change key), atomic
   put/get round-trip, None-not-cached.
2. **tests/cli/test_ingest_incremental.py** — the FR-5-AC-4 two-run scenario with counting
   fake predictors + `FakeEmbedClient`: run1 full (N LLM calls); edit supply CSV (add 1,
   remove 1); run2 → 0 resume/feedback LLM calls, unchanged candidates' gold_hash byte-stable,
   gold retains `profile_summary` (G-1 regression), tombstone + revive, index
   skipped_unchanged == unchanged count.
3. **tests/web/test_supply.py** — GET parses fixture sheets + status join; POST appends
   (re-parse verifies), 409 dup, 422 category fields; DELETE removes + 404; banner bumped.
4. **tests/web/test_attachments.py** — resume stored + link_check both ways; feedback write
   produces a blob that `parse_markdown` links to the email (round-trip through the real
   parser); traversal guard; delete.
5. **tests/web/test_ingest_job.py** — 202 + single-flight 409 (subprocess monkeypatched);
   status transitions; summary parse from a canned CLI transcript; failure → log tail.
6. Tier-1 invariants untouched (no scoring-path change); `make eval` unaffected.
