# Requirements — a-001 Ingest Landing & Parse (bronze layer)

> Slice 1 of the ingest phase: the deterministic landing + parsing foundation. Raw files
> become an immutable, content-addressed **bronze** layer and are parsed into typed
> `BronzeRecord`s — no LLM, no normalization, no identity resolution.
> References: AD-003, AD-065 (CSV snapshots), AD-066 (bronze/silver/gold), AD-070 (snapshot
> as-of), and `ee-ingestion-architecture.md` §1, §2, §4, §5 (steps 1–2), §6 (Phase 1/2),
> §10, §12, §13.

## User story

As a data engineer building the matcher's corpus, when I run ingest over a directory of raw
supply CSVs, resume PDFs, and feedback Markdown, I want every file landed verbatim into an
immutable content-addressed store and parsed into typed records — deterministically, with
duplicates skipped, malformed inputs logged and counted (never silently dropped or silently
passed), and the whole run replayable from bronze — so that every downstream derivation has a
single auditable source of truth and the same bytes always produce the same result.

## Scope note (resolved at sign-off)

`docs/progress.A.md` "Next up" said "keep ingest sheets-only in Slice 1; defer Docling to
Slice 2." **Resolved (2026-06-18): CSV, PDF, and Markdown bronze parsing all land in this
slice.** This spec follows the **task brief**, which puts all three in scope while deferring all
*enrichment* (LLM extraction, normalization) to later slices — bronze PDF parsing (Docling →
section-tagged verbatim text) is distinct from enrichment. The "sheets-only" lane note is
superseded — this is scope sequencing, not an architectural decision, so it is recorded in the
Lane A progress file rather than as an ADR.

## Out of scope (later slices — do not implement)

Normalization, `candidate_id`/HMAC, taxonomy, silver/gold, LLM enrich, embedding, PII
redaction/leak-scan/vault, merge, snapshot reconciliation/tombstones, freshness-guard
enforcement, the `dsm ingest` CLI command wiring.

## Acceptance criteria (EARS format)

### Data layout & gitignore (L-LAYOUT)

**L-LAYOUT-1** The repository SHALL provide the directory layout `data/raw/{supply,resumes,feedback}/`, `data/bronze/{blobs/sha256/}`, and `data/.cache/`, each kept under version control via a `.gitkeep` placeholder.

**L-LAYOUT-2** WHEN bronze blobs or records are written, the system SHALL write them under `data/bronze/blobs/sha256/<hash>` and `data/bronze/records/<hash>.jsonl`, and the manifest to `data/bronze/manifest.jsonl`.

**L-LAYOUT-3** Bronze blobs, bronze records, and the manifest are PII-dense and SHALL be gitignored (only the directory placeholders are tracked). The spec SHALL document the encryption-at-rest boundary (LocalFS MVP plaintext-on-disk vs object-store-with-IAM later) without implementing remote encryption.

### Blob store (B)

**B-PUT-1** WHEN `BlobStore.put(data)` is called, the system SHALL compute `sha256(data)`, store the bytes addressed by that hash, and return the hash string in the form `"sha256:<hex>"`.

**B-PUT-2** WHEN `put` is called twice with byte-identical input, the system SHALL produce the identical hash and SHALL NOT corrupt or duplicate the stored blob (idempotent).

**B-PUT-3** WHEN `put` writes a blob, it SHALL write to a temporary file and atomically rename into place, so a crash mid-write never leaves a partial blob at the final path.

**B-GET-1** WHEN `BlobStore.get(hash)` is called for a stored hash, the system SHALL return the exact original bytes.

**B-EXISTS-1** `BlobStore.exists(hash)` SHALL return `True` iff a blob for that hash is stored, else `False`.

**B-PROTO-1** `BlobStore` SHALL be a `typing.Protocol`; `LocalFSBlobStore` SHALL implement it, so the backend is swappable (LocalFS now, object store later) per AD-066.

### Manifest (M)

**M-APPEND-1** WHEN `Manifest.append(entry)` is called, the system SHALL serialize the `ManifestEntry` as one JSON object on its own line appended to `manifest.jsonl` (append-only; existing lines never rewritten).

**M-APPEND-2** The append SHALL be the **commit marker**: a `raw_bytes_hash` is considered landed only once its `ManifestEntry` line exists (per §10).

**M-HASH-1** `Manifest.has_hash(raw_bytes_hash)` SHALL return `True` iff a prior entry with that hash AND `status == LANDED` exists, else `False`.

**M-RUN-1** `Manifest.entries_for_run(run_id)` SHALL return all entries whose `run_id` matches, in append order.

**M-PROTO-1** `Manifest` SHALL be a `Protocol`; `JSONLManifest` SHALL implement it (swappable to SQLite later per AD-066).

### Landing (LAND)

**LAND-DISCOVER-1** WHEN landing runs over `data/raw/`, the system SHALL discover every file under `supply/`, `resumes/`, and `feedback/` in a **deterministic order** (sorted by path).

**LAND-CLASSIFY-1** The system SHALL classify each file to a `SourceType` deterministically: `supply/beach.csv → SUPPLY_BEACH`, `supply/rolling_off.csv → SUPPLY_ROLLING_OFF`, `supply/new_joiners.csv → SUPPLY_NEW_JOINERS`, `resumes/*.pdf → RESUME`, `feedback/*.md → FEEDBACK`.

**LAND-CLASSIFY-2** WHEN a file cannot be classified (unknown directory, unexpected extension, or unrecognized supply filename), the system SHALL record a `ManifestEntry` with `status=INVALID`, log it (reason + payload + run_id), count it, and SHALL NOT write a blob.

**LAND-HASH-1** The system SHALL hash each file's raw bytes with sha256; identical bytes SHALL always yield the identical hash.

**LAND-DEDUP-1** WHEN a file's content hash already returns `has_hash == True`, the system SHALL record a `ManifestEntry` with `status=SKIPPED`, SHALL NOT re-write the blob, and SHALL count it as skipped (idempotent re-land).

**LAND-WRITE-1** WHEN a file's content hash is new, the system SHALL write the blob FIRST, THEN append a `ManifestEntry` with `status=LANDED` (this ordering is the recovery invariant).

**LAND-ASOF-1** WHEN landing a supply CSV, the system SHALL parse the `as of <date>` banner into the entry's `snapshot_date`; for `RESUME`/`FEEDBACK`, `snapshot_date` SHALL be `None`.

**LAND-ASOF-2** WHEN a supply CSV has no parseable banner, `snapshot_date` SHALL be `None` and a parse warning SHALL be logged — landing the blob still succeeds (the banner is metadata, not a gate at this slice).

### Recovery / determinism (D)

**D-RECOVER-1** WHEN a crash occurs after the blob is written but before the manifest entry is appended, a subsequent run SHALL re-land the same file (`has_hash` is still `False`), `put` SHALL be a no-op-equivalent on the already-present blob, and the manifest entry SHALL then be appended — leaving exactly one LANDED entry and one blob (no duplication, no corruption).

**D-IDEMPOTENT-1** WHEN the identical raw directory is landed twice in succession, the second run SHALL produce zero new blobs and SHALL mark every file `SKIPPED`.

**D-DETERMINISM-1** Given identical input bytes, parsing SHALL produce byte-identical `BronzeRecord` sequences (same order, same `row_index`, same `raw` values).

### CSV parsing (C)

**C-BANNER-1** WHEN parsing a supply CSV whose first line matches `as of <date>`, the system SHALL extract that date as the snapshot date and SHALL NOT treat the banner line as a data row or header.

**C-HEADER-1** The system SHALL treat the first non-banner row as the column header and map each subsequent data row to `{column_name: cell_value}` with values kept **verbatim as strings** (zero normalization, original column names preserved).

**C-QUOTE-1** The system SHALL honor standard CSV quoting (quoted fields containing commas, quotes, and newlines parse as single cells).

**C-ROW-1** WHEN a data row is parsed, the system SHALL emit a `BronzeRecord` with `source_type`, `source_hash`, a monotonically increasing `row_index` (0-based over data rows), and `raw` = the column→value map.

**C-INVALID-1** WHEN a data row is malformed (e.g. column count does not match the header, or is unparseable), the system SHALL log it (reason + payload + run_id), skip it, increment the invalid count, and continue — it SHALL NOT abort the file and SHALL NOT emit a record for that row.

### PDF parsing (P)

**P-TEXT-1** WHEN parsing a resume PDF with extractable text, the system SHALL produce exactly one `BronzeRecord` with `raw = {"text": <full text>, "sections": [<section tags>], "email_found": <first email or "">}` (verbatim text; no LLM, no normalization).

**P-EMAIL-1** The system SHALL detect the first email address in the document text by deterministic regex and place it in `email_found`; WHEN none is present, `email_found` SHALL be `""`.

**P-OCR-1** WHEN the PDF yields no extractable text directly, the system SHALL attempt the OCR fallback path before giving up.

**P-INVALID-1** WHEN a PDF yields no text even after OCR fallback (or cannot be opened), the system SHALL log it (reason + payload + run_id), skip it, count it invalid, and emit no record.

### Markdown parsing (MD)

**MD-KEY-1** WHEN parsing a feedback Markdown file, the system SHALL extract the email key that identifies the consultant the feedback is about.

**MD-SPLIT-1** The system SHALL split the document into one or more feedback items, emitting one `BronzeRecord` per item with `raw = {"email_key": <email>, "raw_markdown": <verbatim item markdown>, "kind": "project"|"client"}` and an incrementing `row_index`.

**MD-INVALID-1** WHEN a feedback file has no extractable email key, the system SHALL log it (reason + payload + run_id), skip the whole file, count it invalid, and emit no records.

### Observability & lineage (O)

**O-RUN-1** A landing run SHALL produce a typed run manifest recording `run_id`, the files seen with their hashes, and counts of `landed` / `skipped` / `invalid`.

**O-LOG-1** Every invalid (unclassifiable file, malformed row, unparseable PDF, keyless feedback) SHALL be logged via structlog with `reason`, `payload`, and `run_id` — never silently passed and never quarantined.

**O-LOG-2** Logs SHALL NOT emit raw PII payloads to any external sink; structlog output is local (per tech.md §coding-standards). The invalid `payload` is logged for local diagnosis only.

### Boundaries (NF)

**NF-TYPED-1** Every value crossing a module boundary (`ManifestEntry`, `BronzeRecord`, run manifest, blob hash) SHALL be a Pydantic model or typed primitive — no dicts-as-contracts (golden rule 4). `BronzeRecord.raw` is intentionally `dict[str, str | list[str]]` per the frozen Phase-2 contract.

**NF-IMPORT-1** `dsm/ingest/` SHALL import nothing from `dsm.match`, `dsm.index`, `dsm.pii`, `dspy`, `modal`, or `httpx`; this SHALL be enforced by an import-linter contract.

**NF-NONET-1** No parsing, landing, or test SHALL make a network call. Docling/OCR run locally; tests use local fixtures only.

## Non-requirements

- Silver/gold/normalization/enrichment/embedding — later slices.
- `candidate_id`, HMAC, taxonomy mapping, PII redaction/vault/leak-scan — later slices.
- Snapshot reconciliation, tombstones, freshness-guard enforcement — `snapshot_date` is
  parsed and stamped only; nothing acts on it yet.
- SQLite manifest / object-store blob backend — the protocols exist; only LocalFS + JSONL
  are implemented now.
- Wiring landing/parse into the `dsm ingest` CLI command.
