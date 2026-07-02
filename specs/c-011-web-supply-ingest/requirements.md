# c-011 — Web supply ingest (requirements)

## User story
As the staffing operator, I want to manage the consultant supply pool **from the web page** —
see all three categories (beach / rolling off / new joiners) in one table, add or remove a
candidate inline, attach a resume PDF and feedback (written in Markdown or uploaded as a file)
per row, and press **one button** to run the ingest pipeline — so the index reflects my edits
without touching the CLI, and **without re-doing expensive work** (LLM enrichment, embeddings)
for candidates whose data did not change.

## Context / defect being fixed alongside
Two incrementality gaps in `dsm ingest` become critical once the web UI makes partial edits
routine (today they are masked by full clear-and-re-ingest usage):

- **G-1 (enrichment loss):** `merge_run` merges only records normalized **this run** (landed
  blobs). Editing one supply CSV re-lands only that CSV; the (unchanged, SKIPPED) resume and
  feedback blobs are not re-normalized, so every candidate on the edited sheet gets their gold
  **overwritten without** `profile_summary` / feedback / resume citations. Silent data loss.
  (`dsm/cli/commands.py` parse loop skips non-LANDED entries; `merge_run` at
  `dsm/ingest/merge.py:261` takes only the in-memory list.)
- **G-2 (no enrich cache):** every resume/feedback record that *is* normalized in a run goes to
  the LLM, even when the same content was enriched before under the same
  `(prompt_version, model_version)`. §11 of the ingestion design says a version bump "forces
  re-extraction" — implying unchanged content should **not** re-extract — but no content-keyed
  gate exists. (Enrich loop at `dsm/cli/commands.py:1384–1415`; only DSPy's global exact-call
  cache helps incidentally.)

## Functional requirements (EARS)

### FR-1 — Supply read
- **FR-1-AC-1** WHEN the client calls `GET /supply`, the system SHALL return, for each of the
  three categories, the sheet's banner `as_of` date and its rows parsed from the **current raw
  supply CSVs** (`data/raw/supply/*.csv`), each row carrying at minimum name, email, grade,
  skills, location, and the category-specific availability fields.
- **FR-1-AC-2** Each returned row SHALL carry sync status derived from the gold store:
  `ingested` (a live gold entity exists for the row's `candidate_id`), `has_resume`
  (a resume source is linked in silver, same resolution as `/resume`), and `feedback_count`
  (feedback entries on the gold entity); a row with no gold entity SHALL read `ingested=false`
  (UI shows "pending ingest").
- **FR-1-AC-3** WHEN a supply CSV is absent or a row is malformed, the system SHALL skip and
  report it in a `skipped` list — never abort the whole response (mirrors C-INVALID-1).

### FR-2 — Add / remove a candidate row
- **FR-2-AC-1** WHEN the client `POST /supply/candidates` with a category and the row fields,
  the system SHALL append a well-formed row to that category's CSV, preserving that sheet's
  existing header schema, and SHALL update the banner `as of` date to today.
- **FR-2-AC-2** IF a row with the same email already exists in that category, the system SHALL
  reject with 409 (the email is the identity join key, AD-012/067) — no duplicate rows.
- **FR-2-AC-3** WHEN the client `DELETE /supply/candidates/{category}/{email}`, the system
  SHALL remove the matching row (by `Email` column, case-insensitive) and update the banner
  date; a missing row SHALL return 404.
- **FR-2-AC-4** Category-specific required fields SHALL be validated server-side: rolling off
  requires a roll-off date + confidence (high/medium/low); new joiners require a join date;
  invalid input SHALL return 422 with a field-level message.

### FR-3 — Resume and feedback attachments
- **FR-3-AC-1** WHEN the client `POST /supply/candidates/{email}/resume` with a PDF, the system
  SHALL write it to `data/raw/resumes/` under a deterministic name derived from the email local
  part (replace-on-re-upload), and reject non-PDF uploads with 422.
- **FR-3-AC-2** Because resume→candidate linking is by the **first email found in the PDF
  text** (`parse/pdf.py`), the response SHALL carry `link_check: ok|no_email_found` by scanning
  the uploaded bytes' extractable text for the candidate email, so the UI can warn *before*
  ingest that a PDF will not link.
- **FR-3-AC-3** WHEN the client `POST /supply/candidates/{email}/feedback` with either written
  Markdown text (+ source: internal/client) or an uploaded `.md`/`.txt` file, the system SHALL
  write a feedback Markdown file to `data/raw/feedback/` that is guaranteed to link: an
  `email: <candidate email>` key line and a `## <Client|Project> feedback` heading are ensured
  (prepended when the payload lacks them, per `parse/markdown.py` rules). Multiple feedback
  files per candidate SHALL coexist (append semantics — never overwrite an earlier entry).
- **FR-3-AC-4** WHEN the client `DELETE .../resume` or `DELETE .../feedback/{filename}`, the
  system SHALL delete the raw file (the next ingest run then reflects the removal in gold);
  deleting a file outside `data/raw/{resumes,feedback}` SHALL be impossible (path-traversal
  guarded).

### FR-4 — Ingest trigger
- **FR-4-AC-1** WHEN the client `POST /ingest/run`, the system SHALL start `dsm ingest`
  followed by `dsm index` as a **background job** and return 202 with a job id immediately;
  `GET /ingest/status` SHALL report `idle | running | succeeded | failed`, the captured
  pipeline summary (landed/skipped, gold updated/unchanged/tombstoned, indexed/
  skipped-unchanged/removed), and a log tail on failure.
- **FR-4-AC-2** IF a job is already running, `POST /ingest/run` SHALL return 409 (single-flight).
- **FR-4-AC-3** The web layer SHALL NOT reimplement any pipeline stage — it invokes the same
  ingest/index composition the CLI runs (golden rule: one spine).

### FR-5 — Incremental ingest correctness (fixes G-1/G-2)
- **FR-5-AC-1** WHEN `dsm ingest` runs, the merge SHALL be computed over the **full current raw
  corpus** (every entry LANDED **or** SKIPPED this run — i.e. every file currently present),
  not only newly-landed blobs, so a partial edit never drops enrichment from unchanged
  candidates' gold. Bronze records for SKIPPED blobs SHALL be read from the persisted bronze
  store (no PDF re-parse); a missing records file falls back to `parse_blob`.
- **FR-5-AC-2** WHEN a resume/feedback record is enriched, the extraction SHALL be cached
  keyed by `(candidate_id, source_hash, prompt_version, model_version)`; a later run with the
  same key SHALL reuse the cached extraction and make **zero LLM calls** for it. A
  `prompt_version` or `model_version` bump invalidates (re-extracts) — the §11 rule, now real.
- **FR-5-AC-3** Gold SHALL be written only when the entity's `gold_hash` changed (or its
  tombstone state flips); an idempotent re-run SHALL write nothing and say so in the summary.
- **FR-5-AC-4** The two-run scenario "run full, then add one candidate + remove one via the
  supply CSV, run again" SHALL produce: the new candidate merged + indexed; the removed one
  tombstoned + deleted from the index; **zero enrich-LLM calls and zero re-embeds for the
  unchanged candidates** (index skip via the existing `(gold_hash, model_version)` gate,
  AD-082). Machine-verified by test.
- **FR-5-AC-5** Existing reconcile semantics SHALL be preserved: tombstone on departure,
  revive on re-add (AD-093/094), leak-scan abort (AD-069), quality gate, and the
  no-supply-roster guard.

### FR-6 — Frontend
- **FR-6-AC-1** The page SHALL have two primary tabs: **Supply & Ingest** (this slice) and
  **Open Roles & Match** (the existing c-008 UI, unchanged behaviour).
- **FR-6-AC-2** The supply tab SHALL show all three categories as grouped sections of one
  table with live counts; inline add row (ghost row → editable row → save/cancel); per-row
  resume upload/replace/remove and feedback write-Markdown/upload/remove, matching the
  approved mockup; per-row sync status chip (`synced` / `pending ingest`).
- **FR-6-AC-3** A **Run ingest** button SHALL trigger `POST /ingest/run`, poll status, and
  render the returned incremental summary (so the operator *sees* "9 unchanged — skipped").
- **FR-6-AC-4** All server-provided strings SHALL be escaped / carried via `data-*` attributes
  (no inline-JS interpolation) — the c-008 XSS rule.

## Non-functional
- **NF-1** No PII to any external service from the web layer; `dsm.web` still imports no
  `dsm.pii` (candidate-id derivation goes through a `dsm.cli.commands` helper, mirroring the
  c-008 composition-root reuse). The enrich cache stores the same content gold already stores,
  under the same gitignored `data/` root.
- **NF-2** Gates stay deterministic and untouched. Frozen `dsm/models.py` untouched (web-local
  DTOs + ingest-local cache model only).
- **NF-3** `make check` green; new behaviour covered by tests (no live LLM/network in unit
  tests — fake predictors count calls).
- **NF-4** Concurrency: file writes to supply CSVs are atomic (temp + rename); the ingest job
  is single-flight. Milvus Lite single-connection caveat documented (index step may fail if a
  match request holds the store at that instant → job reports failed; re-run).
