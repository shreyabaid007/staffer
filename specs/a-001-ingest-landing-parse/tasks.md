# Tasks — a-001 Ingest Landing & Parse (bronze layer)

> Ordered, atomic, independently testable. Each maps to acceptance criteria in requirements.md.
> One task = one commit (imperative, referencing the spec). `make check` green before each commit.

## Task list

### T-001: Scaffold `data/` layout + gitignore

**Files:** `data/raw/{supply,resumes,feedback}/.gitkeep`, `data/bronze/{blobs/sha256,records}/.gitkeep`, `data/.cache/.gitkeep` (exists), `.gitignore`.

Create the directory layout. Gitignore bronze blobs, bronze records, and `manifest.jsonl`
(keep the `.gitkeep` placeholders tracked). Add a short `data/README.md` documenting the
encryption-at-rest boundary (LocalFS MVP plaintext vs object-store later) per L-LAYOUT-3.

**Acceptance:** layout exists; `git status` shows no bronze data would be committed; `.gitkeep`s tracked. `make check` green.

**Criteria:** L-LAYOUT-1, L-LAYOUT-2, L-LAYOUT-3.

---

### T-002: Ingest models

**File:** `dsm/ingest/models.py`, `tests/ingest/test_models.py`.

Define `SourceType`, `LandingStatus`, `ManifestEntry`, `BronzeRecord`, `RunManifest` exactly per
design.md (frozen; `StrEnum`; §6 contracts reproduced; `source_type`/`raw_bytes_hash` optional
for the INVALID case). Tests: instantiation, frozen-ness, INVALID entry with `None` fields,
`BronzeRecord.raw` accepts `str` and `list[str]` values.

**Acceptance:** models import; tests pass; `make check` green.

**Criteria:** NF-TYPED-1 (data foundation for all others).

---

### T-003: BlobStore protocol + LocalFS impl

**File:** `dsm/ingest/blobstore.py`, `tests/ingest/test_blobstore.py`.

`BlobStore` Protocol + `LocalFSBlobStore`: `put` (sha256, `"sha256:"`-prefixed return, atomic
temp+rename), `get`, `exists`. Tests (tmp_path): put→get round-trip byte-identical; put-twice
idempotent (one file, same hash); hash format; exists True/False; empty-bytes put.

**Acceptance:** tests cover B-PUT-1/2/3, B-GET-1, B-EXISTS-1, B-PROTO-1. `make check` green.

**Criteria:** B-PUT-1, B-PUT-2, B-PUT-3, B-GET-1, B-EXISTS-1, B-PROTO-1.

---

### T-004: Manifest protocol + JSONL impl

**File:** `dsm/ingest/manifest.py`, `tests/ingest/test_manifest.py`.

`Manifest` Protocol + `JSONLManifest`: append (one JSON line, append-only), `has_hash` (True iff
prior LANDED entry with that hash), `entries_for_run`. Tests (tmp_path): append then read back;
`has_hash` ignores SKIPPED/INVALID entries; `entries_for_run` filters + preserves order;
round-trips `ManifestEntry` including `snapshot_date=None`.

**Acceptance:** tests cover M-APPEND-1/2, M-HASH-1, M-RUN-1, M-PROTO-1. `make check` green.

**Criteria:** M-APPEND-1, M-APPEND-2, M-HASH-1, M-RUN-1, M-PROTO-1.

---

### T-005: Landing (discover · classify · hash · dedup · write · recovery)

**File:** `dsm/ingest/land.py`, `tests/ingest/test_land.py`.

Pure `classify(path) -> SourceType | None` (table-driven). `land(raw_root, blobs, manifest,
run_id)`: deterministic sorted discovery; classify; hash; dedup via `has_hash`; supply as-of via
`read_banner_date` (added in T-006 — for this task stub it to return `None`, wire fully in T-006);
**blob write before manifest append**; INVALID for unclassifiable. Tests (tmp_path + tiny
synthetic files): classify table; new file → LANDED + blob exists; dedup → SKIPPED, no new blob;
unclassifiable → INVALID, no blob; **D-RECOVER-1** (pre-place blob, no manifest entry → re-land
yields one LANDED entry, one blob); **D-IDEMPOTENT-1** (land twice → all SKIPPED, 0 new blobs).

**Acceptance:** tests cover LAND-DISCOVER-1, LAND-CLASSIFY-1/2, LAND-HASH-1, LAND-DEDUP-1, LAND-WRITE-1, D-RECOVER-1, D-IDEMPOTENT-1. `make check` green.

**Criteria:** LAND-DISCOVER-1, LAND-CLASSIFY-1, LAND-CLASSIFY-2, LAND-HASH-1, LAND-DEDUP-1, LAND-WRITE-1, D-RECOVER-1, D-IDEMPOTENT-1.

---

### T-006: CSV parser + banner/as-of + golden fixtures

**Files:** `dsm/ingest/parse/__init__.py`, `dsm/ingest/parse/csv.py`, `tests/fixtures/ingest/*.csv`, `tests/ingest/parse/test_csv.py`. Wire `read_banner_date` into `land.py` (LAND-ASOF-1/2).

`read_banner_date(data) -> date | None`; `parse_csv(data, source_type, source_hash, *, run_id)
-> list[BronzeRecord]` using stdlib `csv` (quoting), de-bannered header + verbatim string rows,
0-based `row_index`. Malformed rows → `log_invalid` + skip + count (uses `lineage` from T-009 —
for this task a thin local logger is fine; consolidate in T-009). Golden fixtures: banner CSV,
quoted-field CSV, malformed-row CSV, no-banner CSV. Tests assert exact expected `BronzeRecord`
lists + determinism (parse twice → identical).

**Acceptance:** tests cover C-BANNER-1, C-HEADER-1, C-QUOTE-1, C-ROW-1, C-INVALID-1, LAND-ASOF-1/2, D-DETERMINISM-1. `make check` green.

**Criteria:** C-BANNER-1, C-HEADER-1, C-QUOTE-1, C-ROW-1, C-INVALID-1, LAND-ASOF-1, LAND-ASOF-2, D-DETERMINISM-1.

---

### T-007: PDF parser (Docling → sections + email_found, OCR fallback)

**Files:** `dsm/ingest/parse/pdf.py`, `tests/fixtures/ingest/*.pdf`, `tests/ingest/parse/test_pdf.py`. Route `RESUME` in `parse/__init__.py`.

`parse_pdf(data, source_hash, *, run_id) -> list[BronzeRecord]`: Docling extract → `text` +
`sections` + `email_found` (deterministic regex, `""` if none); OCR fallback when text empty;
no-text/open-failure → `log_invalid` + skip + count. One record per PDF (`row_index=0`). Tests:
golden text-PDF fixture → expected record; no-email PDF → `email_found=""`; empty/image PDF →
invalid logged+skipped+counted (OCR path exercised via local fixture or a Docling stub — no
network).

**Acceptance:** tests cover P-TEXT-1, P-EMAIL-1, P-OCR-1, P-INVALID-1, NF-NONET-1. `make check` green.

**Criteria:** P-TEXT-1, P-EMAIL-1, P-OCR-1, P-INVALID-1.

---

### T-008: Markdown feedback parser (email key + split items)

**Files:** `dsm/ingest/parse/markdown.py`, `tests/fixtures/ingest/*.md`, `tests/ingest/parse/test_markdown.py`. Route `FEEDBACK` in `parse/__init__.py`.

`parse_markdown(data, source_hash, *, run_id) -> list[BronzeRecord]`: extract email key; split
into feedback items (delimiter rule documented in code + test); one record per item with
`{email_key, raw_markdown, kind}`; `kind` derived deterministically; no-key → `log_invalid` +
skip whole file + count. Fixtures: multi-item feedback w/ email, single-item, no-email-key.

**Acceptance:** tests cover MD-KEY-1, MD-SPLIT-1, MD-INVALID-1. `make check` green.

**Criteria:** MD-KEY-1, MD-SPLIT-1, MD-INVALID-1.

---

### T-009: Lineage seed (run manifest + structlog invalid logging) + structlog dep + import contract

**Files:** `dsm/ingest/lineage.py`, `pyproject.toml`, `tests/ingest/test_lineage.py`. Replace the thin local loggers from T-006/T-007/T-008 with `lineage.log_invalid`.

Add `structlog` to `[project.dependencies]`. Add the `dsm.ingest` forbidden import contract
(design.md). Implement `log_invalid(run_id, reason, payload, source_uri=None)` (structlog, local,
no PII to network) and `build_run_manifest(run_id, entries) -> RunManifest` tallying
landed/skipped/invalid. Tests: run manifest counts from a mixed entry list; `log_invalid`
captured via structlog test capture with `reason`/`payload`/`run_id` present.

**Acceptance:** tests cover O-RUN-1, O-LOG-1, O-LOG-2; import-linter passes the new contract; `uv lock` updated. `make check` green.

**Criteria:** O-RUN-1, O-LOG-1, O-LOG-2, NF-IMPORT-1.

---

### T-010: End-to-end landing→parse integration + final verification

**Files:** `tests/ingest/test_land_parse_e2e.py`, all modified files.

Integration test over a small synthetic `raw/` dir (supply CSV + resume PDF + feedback MD):
land all → parse each blob → assert `RunManifest` counts and the full `BronzeRecord` set;
re-land → all SKIPPED. Then: run `make check` fully green (format, lint, typecheck, all tests,
both import contracts); verify `dsm.ingest` imports nothing from match/index/pii/LLM; confirm
every EARS criterion is covered by a test; confirm no network call in any test.

**Acceptance:** `make check` fully green; all acceptance criteria mapped to a passing test; no network in tests.

**Criteria:** all (integration + NF-IMPORT-1, NF-NONET-1).
