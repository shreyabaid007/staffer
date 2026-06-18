# Design — a-001 Ingest Landing & Parse (bronze layer)

> Technical design for the deterministic landing + parsing foundation.
> References: requirements.md in this folder; AD-003, AD-065, AD-066, AD-070;
> `ee-ingestion-architecture.md` §1/§2/§4/§5/§6/§10/§12/§13; `docs/structure.md` dep rules.

## Modules touched (new)

| Module | Responsibility | Phase contract |
| --- | --- | --- |
| `dsm/ingest/models.py` | `SourceType`, `LandingStatus`, `ManifestEntry`, `BronzeRecord`, `RunManifest` | typed contracts |
| `dsm/ingest/blobstore.py` | `BlobStore` protocol + `LocalFSBlobStore` | `put/get/exists` by sha256 |
| `dsm/ingest/manifest.py` | `Manifest` protocol + `JSONLManifest` | `append/has_hash/entries_for_run` |
| `dsm/ingest/land.py` | discover · classify · hash · dedup · write blob · append manifest | `raw dir → list[ManifestEntry]` (Step 1) |
| `dsm/ingest/parse/__init__.py` | parse router (`source_type → parser`) | `bronze blob → list[BronzeRecord]` (Step 2) |
| `dsm/ingest/parse/csv.py` | banner/as-of · headers · quoting · row records · log+skip | CSV → `list[BronzeRecord]` |
| `dsm/ingest/parse/pdf.py` | Docling → section text + `email_found` · OCR fallback · log+skip | PDF → `list[BronzeRecord]` |
| `dsm/ingest/parse/markdown.py` | email key · split feedback items · log+skip | MD → `list[BronzeRecord]` |
| `dsm/ingest/lineage.py` | run manifest assembly + structlog invalid-record logging | observability seed |

Existing `dsm/ingest/stub.py` stays untouched (still used by the CLI for the match pipeline);
this slice adds the real landing/parse foundation alongside it, not wired into the CLI yet.

## Data contracts (`dsm/ingest/models.py`, module-local per structure.md)

Models use `StrEnum` and `BaseModel` to match `dsm/models.py` house style. `ManifestEntry`
and `BronzeRecord` are the **frozen Phase-1/Phase-2 contracts from §6** — reproduce them
exactly; do not rename fields.

```python
from datetime import date, datetime
from enum import StrEnum
from pydantic import BaseModel

class SourceType(StrEnum):
    SUPPLY_BEACH = "supply_beach"
    SUPPLY_ROLLING_OFF = "supply_rolling_off"
    SUPPLY_NEW_JOINERS = "supply_new_joiners"
    RESUME = "resume"
    FEEDBACK = "feedback"

class LandingStatus(StrEnum):
    LANDED = "landed"      # new bytes, stored
    SKIPPED = "skipped"    # content hash already seen (idempotent no-op)
    INVALID = "invalid"    # could not classify or read

class ManifestEntry(BaseModel, frozen=True):
    run_id: str
    source_uri: str
    source_type: SourceType | None          # None when unclassifiable (status=INVALID)
    raw_bytes_hash: str | None               # "sha256:..."; None when not read
    size_bytes: int
    discovered_at: datetime
    snapshot_date: date | None = None        # parsed from CSV banner; None for pdf/md/invalid
    status: LandingStatus

class BronzeRecord(BaseModel, frozen=True):
    source_hash: str
    source_type: SourceType
    row_index: int
    raw: dict[str, str | list[str]]          # CSV: col→value; PDF: text/sections/email_found;
                                             # MD: email_key/raw_markdown/kind

class RunManifest(BaseModel, frozen=True):
    run_id: str
    entries: list[ManifestEntry]
    landed: int
    skipped: int
    invalid: int
```

**Note on §6 divergence (resolved at sign-off, 2026-06-18):** the architecture types
`source_type`/`raw_bytes_hash` as non-optional, but INVALID entries (unclassifiable / unreadable
files) have neither. **Decision: make both `| None`** for the INVALID case — the minimal honest
contract (a separate `InvalidEntry` model and sentinel values were both considered and rejected).

## blobstore.py — design

```python
from typing import Protocol

class BlobStore(Protocol):
    def put(self, data: bytes) -> str: ...          # returns "sha256:<hex>"
    def get(self, blob_hash: str) -> bytes: ...
    def exists(self, blob_hash: str) -> bool: ...
```

`LocalFSBlobStore(root: Path)` stores at `root/blobs/sha256/<hex>` (the `"sha256:"` prefix is
stripped for the filename; the prefixed form is the public hash string).

- **Hashing:** `hashlib.sha256(data).hexdigest()`, returned as `f"sha256:{hexdigest}"`.
- **Atomic write (B-PUT-3):** write to `root/blobs/sha256/.tmp-<hex>`, `os.replace` into final
  path. `os.replace` is atomic on a single filesystem.
- **Idempotency (B-PUT-2):** content-addressed path means re-`put` of identical bytes targets
  the same final path; a temp-write + rename over it is safe and yields the same blob.
- **No PII in logs:** never log blob contents.

## manifest.py — design

```python
class Manifest(Protocol):
    def append(self, entry: ManifestEntry) -> None: ...
    def has_hash(self, raw_bytes_hash: str) -> bool: ...
    def entries_for_run(self, run_id: str) -> list[ManifestEntry]: ...
```

`JSONLManifest(path: Path)` — `manifest.jsonl`, one `entry.model_dump_json()` per line.

- **append (M-APPEND):** open in append mode, write `json + "\n"`, flush. This is the commit
  marker — never rewrite prior lines.
- **has_hash (M-HASH-1):** read lines, return `True` iff any parsed entry has matching
  `raw_bytes_hash` AND `status == LANDED`. (SKIPPED/INVALID entries never satisfy `has_hash`,
  so a previously-skipped or invalid file is reconsidered, but a previously-*landed* one is the
  dedup authority.)
- **entries_for_run:** parse all lines, filter by `run_id`, preserve file order.
- At POC scale a full-file scan is fine; the SQLite swap (AD-066) is deferred behind the
  protocol.

## land.py — design (Step 1)

```python
def land(raw_root: Path, blobs: BlobStore, manifest: Manifest, run_id: str) -> list[ManifestEntry]
```

Flow per discovered file (deterministic sorted order, LAND-DISCOVER-1):

1. **classify** `path → SourceType | None` by (parent dir, filename/extension) — pure function,
   table-driven (LAND-CLASSIFY-1). Supply filenames match on a normalized stem (lowercased,
   spaces/`_`/`-` removed) so `Beach.csv` / `Rolling Off.csv` / `New Joiners.csv` classify
   correctly. Unknown → INVALID entry, log+count, no blob (LAND-CLASSIFY-2).
2. read bytes; **hash** = `blobs`-style sha256 (LAND-HASH-1). (Hash helper shared with blobstore
   to guarantee one definition.)
3. **dedup:** `manifest.has_hash(hash)` → SKIPPED entry, no blob write (LAND-DEDUP-1).
4. **as-of:** for supply types, call `parse.csv.read_banner_date(bytes) → date | None` to fill
   `snapshot_date` (LAND-ASOF-1); warn-and-continue if absent (LAND-ASOF-2). PDF/MD → `None`.
5. **write order (LAND-WRITE-1):** `blobs.put(bytes)` FIRST, THEN `manifest.append(LANDED entry)`.

`classify` is a standalone pure function so it is unit-testable without I/O.

### Recovery invariant (D-RECOVER-1)

Blob-before-manifest means: a crash between the two leaves an orphan blob and no LANDED entry.
On re-run `has_hash` is `False`, so the file re-lands; `blobs.put` re-targets the same
content-addressed path (idempotent), then the manifest entry commits. Net result: one blob, one
LANDED entry, no duplicates. An orphan blob with no manifest entry is inert (nothing reads
blobs except via the manifest).

## parse/ — design (Step 2)

Router `parse_blob(record_bytes: bytes, source_type, source_hash, *, run_id) -> list[BronzeRecord]`
dispatches on `source_type`: supply_* → csv, resume → pdf, feedback → markdown. Each parser is
pure over `(bytes, source_hash)` and emits records or logs+skips+counts invalids via `lineage`.

### parse/csv.py

- `read_banner_date(data) -> date | None`: search the first line for an `as of` marker
  *anywhere* in the line (real banners are a title row, date mid-line), then extract the date —
  tolerating a trailing parenthetical (`(synthetic)`) and CSV padding commas; return `None` if
  absent/unparseable (C-BANNER-1, also used
  by land.py for LAND-ASOF).
- Use the stdlib `csv` module on the de-bannered remainder for quoting correctness (C-QUOTE-1).
- First non-banner row = header (C-HEADER-1). Map each subsequent row to `{col: value}` verbatim
  strings; emit `BronzeRecord(source_type, source_hash, row_index=i, raw=...)` (C-ROW-1).
- Malformed row (cell count ≠ header count, or `csv.Error`) → `lineage.log_invalid(...)`, skip,
  count, continue (C-INVALID-1).

### parse/pdf.py

- Docling `DocumentConverter` → document; build `text` (full) and `sections` (section/heading
  tags from Docling's structure). One `BronzeRecord` per PDF, `row_index=0` (P-TEXT-1).
- `email_found`: first match of a deterministic email regex over `text`, else `""` (P-EMAIL-1).
- **OCR fallback (P-OCR-1):** if direct extraction yields empty text, retry with Docling's OCR
  pipeline option enabled. (Docling bundles OCR; no network.)
- No text after OCR / open failure → `lineage.log_invalid`, skip, count, no record (P-INVALID-1).
- Docling import is local-only and obeys NF-IMPORT-1 (Docling is not match/index/pii/LLM).

### parse/markdown.py

- Extract email key (MD-KEY-1): scan for an explicit key line (e.g. front-matter / a leading
  `email:`-style marker) else first email-regex match in the doc.
- Split into feedback items (MD-SPLIT-1): split on the document's item delimiter (top-level
  `##` headings, or `---` rules — whichever the fixtures use; record the chosen rule in code +
  test). One `BronzeRecord` per item: `{"email_key", "raw_markdown", "kind"}`. `kind` is
  derived deterministically from the heading text (`project` default; `client` when the item
  heading names a client review) — verbatim markdown, no normalization.
- No email key → `lineage.log_invalid`, skip whole file, count, no records (MD-INVALID-1).

## lineage.py — design (seed)

```python
def log_invalid(*, run_id: str, reason: str, payload: str, source_uri: str | None = None) -> None
def build_run_manifest(run_id: str, entries: list[ManifestEntry]) -> RunManifest
```

- `log_invalid` uses a module-level structlog logger; counts derive from manifest/record stream,
  not a global mutable counter (determinism). Logs are local; never emit to a network sink and
  never log the pseudonym map (tech.md). The `payload` is truncated/raw-local only (O-LOG-2).
- `build_run_manifest` tallies `landed/skipped/invalid` from the run's `ManifestEntry` list +
  parser invalid count (O-RUN-1).
- This is a **seed**: full quality metrics (unmapped-skill rate, conflict rate, etc., §12) are
  later slices.

## Dependencies & config

- **structlog** is listed in `docs/tech.md` §14 but absent from `pyproject.toml`. Add it to
  `[project.dependencies]` (T-009). It is an approved dep (no new ADR needed).
- **Docling** is already a dependency.
- **Import contract (NF-IMPORT-1):** add to `pyproject.toml`:
  ```toml
  [[tool.importlinter.contracts]]
  name = "Ingest must not depend on match, index, pii, or LLM code"
  type = "forbidden"
  source_modules = ["dsm.ingest"]
  forbidden_modules = ["dsm.match", "dsm.index", "dsm.pii", "dspy", "modal", "httpx"]
  ```
  (The existing `dsm.ingest → modal/httpx` forbidden contract is subsumed but kept.)

## Encryption-at-rest boundary (documentation only — L-LAYOUT-3)

Bronze blobs/records hold raw PII-dense bytes. **MVP:** plaintext on local disk, gitignored —
the trust boundary is the developer's machine. **Later (AD-066/§4):** object storage where
encryption-at-rest + IAM provide the PII-at-rest control. This slice does **not** implement
remote/at-rest encryption; it only documents the boundary and keeps the `BlobStore` protocol
swap-ready.

## Eval cases to add (golden fixtures, deterministic, no network)

| Case | Fixture | Invariant tested |
| --- | --- | --- |
| CSV golden | `beach.csv` w/ banner | banner→date, headers, verbatim rows → expected `BronzeRecord`s |
| CSV quoting | row with quoted comma/newline | C-QUOTE-1 single-cell parse |
| CSV malformed | row w/ wrong column count | C-INVALID-1 logged + skipped + counted, others survive |
| CSV no banner | header-first CSV | LAND-ASOF-2 / C-BANNER-1 → `snapshot_date=None`, rows still parse |
| PDF golden | tiny text PDF w/ email | P-TEXT-1 / P-EMAIL-1 expected record |
| PDF no-text | image-only / empty PDF | P-INVALID-1 logged + skipped + counted (OCR path exercised/mocked locally) |
| MD golden | feedback w/ email + 2 items | MD-KEY-1 / MD-SPLIT-1 → 2 records, correct `kind` |
| MD no-key | feedback w/o email | MD-INVALID-1 logged + skipped + counted |
| Idempotent re-land | land dir twice | D-IDEMPOTENT-1 second run all SKIPPED, 0 new blobs |
| Commit-marker recovery | blob present, manifest entry absent | D-RECOVER-1 re-land → one blob, one LANDED entry |
| Blob round-trip | put → get | B-PUT-1/B-GET-1 byte-identical, hash form |

All fixtures live under `tests/fixtures/ingest/` and are committed (they are synthetic, not real
PII). Tests mock no network; the OCR path is exercised with a local image-PDF fixture or a
Docling stub if OCR is heavy in CI.

**Opt-in real-data smoke (`tests/ingest/test_real_data_smoke.py`, `make smoke`).** A separate
shape-level smoke over real files in `data/raw/` (PII-dense, gitignored). It runs real Docling,
so it is **excluded from the default suite** — it skips unless `DSM_REAL_SMOKE=1` is set and
files are present. Asserts invariants (0 invalid, every landed file yields ≥1 record, supply
CSVs get a `snapshot_date`, resumes carry `email_found`, feedback carries `email_key`,
re-land is idempotent), not exact records.

## Edge cases

- Duplicate file **content** under different filenames → second is SKIPPED (dedup is by content
  hash, not path).
- Empty file → hash of empty bytes; lands; CSV parse yields zero records (no banner, no rows) —
  not an error.
- CSV banner present but no data rows → zero records, valid run.
- PDF with selectable text but no email → record with `email_found=""` (valid, not invalid).
- Feedback file with email key but a single item → one record (`row_index=0`).
- Unknown file in `raw/supply/` (e.g. `supply/notes.txt`) → INVALID (unrecognized supply file).
- Re-running after adding one new file → only the new file LANDED, rest SKIPPED.
