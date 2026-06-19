# Design — a-002 Ingest Silver & Identity Resolution

> Technical design for the deterministic silver stage. References: `requirements.md` (this folder);
> `ee-ingestion-architecture.md` §3/§5/§6(Phase 3)/§13/§15; AD-032/AD-060/AD-062/AD-063(a)/AD-066/
> AD-067/AD-068/AD-070; `docs/structure.md` dep rules; `docs/tech.md` rules 1/2/6.

## Phase contract

`Silver` is §5 step 3: **`BronzeRecord → NormalizedRecord`**, LLM-free. One typed input, one typed
output, per source (no cross-record merge — that is gold, AD-066/§7).

`valid_as_of` is not on `BronzeRecord` (only `ManifestEntry.snapshot_date` is, from a-001). So the
silver entry point threads the snapshot date in, looked up by `source_hash`:

```python
from dsm.pii.vault import candidate_id        # option (b): permitted by the narrowed NF-IMPORT-1

def normalize(
    record: BronzeRecord,
    *,
    snapshot_date: date | None,          # from a-001 manifest, keyed by record.source_hash
    taxonomy: Taxonomy,                  # canonical skill map + unmapped queue
    run_id: str,
) -> NormalizedRecord | None:            # None = logged+skipped+counted (NF-3)
    # candidate_id(email) imported from dsm.pii.vault (see "Identity & vault placement")
    ...
```

A thin `normalize_run(records, *, snapshot_dates, taxonomy, run_id, extractor_version=...) ->
list[NormalizedRecord]` takes a prebuilt `source_hash → snapshot_date` map (the CLI builds it from
the a-001 manifest entries) and maps `normalize` over records in sorted order, dropping skips (NF-2).
`extractor_version` is a module constant (`SILVER_EXTRACTOR_VERSION = "silver-v1"`).

## Modules touched (new)

| Module | Responsibility | Contract |
| --- | --- | --- |
| `dsm/ingest/models.py` (extend) | add `Grade`, `Confidence`, `NormalizedSkill`, `NormalizedRecord` | ingest-local types |
| `dsm/pii/vault.py` (new, **Lane C** — sign-off item 2 = option b) | `candidate_id = HMAC(email)` derivation + encrypted identity store + `Vault` protocol | deterministic tokenization; ingest imports the derivation |
| `dsm/ingest/silver.py` (new) | type coercion · normalization · per-sheet availability · `normalize`/`normalize_run` · `write_normalized` (silver-layer persistence) | `BronzeRecord → NormalizedRecord`; persist to `data/silver/` |
| `dsm/cli/commands.py` (edit, **Lane C** — precedent: a-001) | wire silver into `dsm ingest`: run after parse, persist, print PII-safe summary | CLI output |
| `.gitignore` (edit) | ignore `data/silver/records/*` (PII-dense, SW-2) | — |
| `dsm/ingest/taxonomy.py` (new) | canonical skill map + alias resolution + unmapped queue | `raw skill → NormalizedSkill` |
| `config/taxonomy.yaml` (new) | canonical skill aliases (tech.md rule 6: maps live in `config/`) | loaded via `dsm/config.py` pattern |
| `dsm/ingest/lineage.py` (extend) | reuse `log_invalid`; add unmapped-skill counter seed | observability |
| `pyproject.toml` (edit) | narrow NF-IMPORT-1 relax: allow `dsm.ingest → dsm.pii.vault` | import contract |
| `dsm/models.py` (edit) | relax `Location.city → str \| None` (AD-075) | frozen-contract edit |

`SourceType` already lives in `dsm/ingest/models.py` (a-001) — reused, not redefined.

## Frozen contract: conflicts & recommendation  ⟵ **SIGN-OFF ITEM 1**

§6's Phase-3 silver sub-models are **stricter-than-frozen in some places and looser in others**.
The frozen `dsm/models.py` (AD-060) is the serving contract; the silver-stage objects are
**pre-canonical** (may be unmapped/unverified, may lack proficiency or a base city). The table maps
each §6 silver type to a resolution that **reuses the frozen type wherever possible** and isolates
genuinely-new state in ingest-local types (per the task's clarification 1).

| §6 silver type | Frozen `dsm/models.py` | Conflict | Recommended resolution |
| --- | --- | --- | --- |
| `ProficiencyLevel` | identical | none | **Import & reuse** as-is. |
| `FreeNow` / `NewJoiner` | identical | none | **Import & reuse** as-is. |
| `RollingOff.confidence: Confidence` | `Literal["high","medium","low"]` | enum vs literal, **values identical** | **Reuse frozen `RollingOff`.** Keep `Confidence` enum ingest-local; feed `Confidence(...).value` into the frozen `Literal`. **No frozen edit.** |
| `AvailabilityState` | identical union | none | **Import & reuse** as-is. |
| `Skill` (optional proficiency + `unmapped`) | `Skill.proficiency` **required**, no `unmapped` | frozen can't carry unmapped / proficiency-less silver skills | **New ingest-local `NormalizedSkill`** (imports `ProficiencyLevel`; adds `unmapped`/`unverified`). **No frozen edit, no redefinition of `Skill`** — this is the "unmapped/unverified-skill handling" clarification 1 sanctions as ingest-local. |
| `Location` (`city: str \| None`) | `Location.city: str` **required** | "Remote (India)" has no base city | **Decision needed (see below).** Recommended: relax `Location.city → str \| None` via a superseding ADR (matches §6; low blast radius). Fallback: keep frozen, use a documented empty-city convention. |

### `NormalizedSkill` (ingest-local — recommended, no frozen edit)
```python
class NormalizedSkill(BaseModel, frozen=True):
    name: str                                    # canonical taxonomy id, or verbatim surface form if unmapped
    proficiency: ProficiencyLevel | None = None  # imports the FROZEN enum; absent for supply / CV-derived
    unmapped: bool = False                       # raw skill not in taxonomy → queued (TX-2)
    unverified: bool = False                     # AD-032: new-joiner CV-derived skill (TX-3)
```
Rationale: silver skills are provenance-tagged and pre-canonical; the architecture itself uses a
*different* model (`MergedSkill`) at gold, so the frozen serving `Skill` is not the right silver
type. `NormalizedSkill` is a **distinct concept**, not a duplicate of `Skill` — it does not violate
structure.md's "no duplicate model definitions".

### `Location.city` — RESOLVED → option (A), relax to optional (AD-075)
"Remote (India)" rows have no base city. **Decision: relax `Location.city: str → str | None = None`**
via the superseding ADR below. Blast radius is small: `match/gates.py` compares
`candidate.location.city == scorecard.location.city` — `None == "Chennai"` is `False`, and
`remote_eligible=True` carries the pass (AD-063(a)), so the gate stays correct; near-miss
`gap_summary` still renders. This is a frozen-contract edit (AD-060) and lands in its own commit
(T-001-FROZEN), with AD-075 appended to `docs/decision.md` in the same commit.

> **ADR-075 · Relax `Location.city` to optional.** *Status: Accepted (2026-06-19).*
> `Location.city` becomes `str | None = None` so the silver stage can represent "Remote (India)"
> consultants with no base city (ee-ingestion §6/§15#3). Supersedes the `city: str` field shape in
> AD-060's frozen `dsm/models.py`. Consumers: gates already pass remote-eligible candidates via
> `remote_eligible`; the `city` comparison degrades safely to `False` for `None`; near-miss
> rendering tolerates `None`. Why: the empty-string sentinel was dishonest typing; §6 already
> anticipated optionality. Consequence: Lane B/C re-pull the contract — one-line, backwards-readable
> change (existing rows with a city are unaffected). Verify `make check` (pyright + tests) stays
> green after the edit.

## Identity & vault placement  ⟵ **SIGN-OFF ITEM 2**

Two settled constraints collide with §13's original `ingest/pii/vault.py` placement:
1. **AD-062 ownership:** `dsm/pii/` is **Lane C**, not Lane A.
2. **NF-IMPORT-1:** `dsm.ingest` is forbidden from importing `dsm.pii` — so a vault under
   `dsm/pii/` is uncallable from ingest without relaxing the import contract (a tech.md rule-1 /
   CLAUDE.md "weaken the PII boundary" STOP trigger).

What silver actually needs **this slice**: only `candidate_id = HMAC(email)` — a deterministic
tokenization, not at-rest crypto. The encrypted vault (name/email store, AD-068) is **only needed at
gold** (to populate `name_vault_ref`/`email_vault_ref`), which is out of scope here.

**RESOLVED → option (b): canonical `dsm/pii/vault.py` + a narrow import relax.** Recorded as ADR-076.

This slice ships:
- `dsm/pii/vault.py`: `candidate_id(email: str) -> str` = `"cid:" + hmac_sha256(key,
  normalize_email(email))`. The HMAC **key is sourced from config/env** (`DSM_CANDIDATE_ID_KEY`),
  never hardcoded; absence fails fast. `candidate_id` is **not PII** (a one-way token). Also exposes
  a `Vault` protocol (`put_identity(candidate_id, name, email) -> tuple[name_ref, email_ref]`) for a
  future encrypted at-rest store. Silver reads the email from the bronze row, derives the id, and
  **drops the raw email** — it is never written to silver (ID-5).
- **Import-contract relax (`pyproject.toml`):** narrow NF-IMPORT-1 so `dsm.ingest` may import
  `dsm.pii.vault` **only**. All other `dsm.pii` submodules (`redact`, `leakscan`,
  `pseudonymised_lm`) stay forbidden to ingest. The relax is a single commit with a note pointing at
  ADR-076. (Import-linter `forbidden` contracts don't support per-submodule allow-lists directly, so
  the contract is restructured: keep the broad ingest-forbidden contract over `dsm.match`/
  `dsm.index`/`dspy`/`modal`/`httpx`, and add a separate `forbidden` contract listing the specific
  PII submodules `dsm.ingest` may not touch — i.e. `dsm.pii.redact`, `dsm.pii.leakscan`,
  `dsm.pii.pseudonymised_lm` — which leaves `dsm.pii.vault` permitted.)

### Cross-lane coordination — RESOLVED (2026-06-19)
`dsm/pii/` is **Lane C** (AD-062), and option (b) makes Lane A (silver) depend on it. **Decision:
Lane A seeds a minimal `dsm/pii/vault.py`** this slice — the `candidate_id` derivation + the `Vault`
protocol + a no-op/in-memory store (the encrypted at-rest impl, AD-068, remains Lane C's to harden
later). **ADR-076 must explicitly record Lane C's agreement** to (i) the placement under their
directory and (ii) Lane A seeding the file — co-ordinated, not a silent cross-lane reach.

## Data contract — `NormalizedRecord` (ingest-local, §6 Phase 3)

```python
from dsm.models import AvailabilityState, FreeNow, Location, NewJoiner, ProficiencyLevel, RollingOff
# ^ FROZEN types imported, never redefined.

class Grade(StrEnum):                 # ingest-local — not in the frozen contract
    SENIOR_CONSULTANT = "senior_consultant"
    LEAD_CONSULTANT = "lead_consultant"
    PRINCIPAL_CONSULTANT = "principal_consultant"

class Confidence(StrEnum):            # ingest-local; values match frozen RollingOff Literal
    LOW = "low"; MEDIUM = "medium"; HIGH = "high"

class NormalizedSkill(BaseModel, frozen=True):   # see "Frozen contract" above
    name: str
    proficiency: ProficiencyLevel | None = None
    unmapped: bool = False
    unverified: bool = False

class NormalizedRecord(BaseModel, frozen=True):
    candidate_id: str                            # HMAC(email); never the raw email (ID-5)
    source_type: SourceType                      # reused from a-001 ingest models
    source_hash: str
    valid_as_of: date | None = None              # from snapshot banner (VAOF-1)
    grade: Grade | None = None
    location: Location | None = None             # FROZEN Location, city now str | None (AD-075)
    availability: AvailabilityState | None = None  # FROZEN union
    skills: list[NormalizedSkill] = Field(default_factory=list)
    raw_text: str | None = None                  # resume body / feedback item → enrichment
    parse_warnings: list[str] = Field(default_factory=list)
    extractor_version: str
```

## silver.py — design

Per `source_type`:
- **supply_beach** → `availability=FreeNow()`; parse grade + location; skills usually empty.
- **supply_rolling_off** → parse roll-off date + confidence → `RollingOff(expected_date, confidence)`.
  Unparseable/missing date → **log+skip+count** (AV-4). Confidence column → `Confidence` →
  `.value` into the frozen `Literal`; unknown confidence → default `"low"` + `parse_warning`.
- **supply_new_joiners** → parse join date → `NewJoiner(join_date)` (missing/invalid → skip, AV-4);
  any skills emitted with `unverified=True` (TX-3).
- **resume / feedback** → `availability=None`; `raw_text` = bronze `text` / `raw_markdown`;
  `candidate_id` from `email_found` / `email_key`; no grade/location/skills at silver (enrich owns
  resume skills). Missing email key here is already an a-001 invalid; defensively skip if absent.

Helpers (pure, unit-testable, no I/O):
- `parse_grade(raw) -> Grade | None` (GR-1)
- `parse_location(location: str, chennai_open: str) -> tuple[Location, list[str]]` — reads **two
  columns** (verified against `data/raw/supply/*.csv`):
  - `city` = `location` value; `None` when `location == "Remote (India)"` (LOC-3, needs AD-075).
  - `remote_eligible` = `(location == "Remote (India)") OR (chennai_open.strip().lower() == "yes")`
    (LOC-NET).
  - `parse_warning` appended when `chennai_open == "Yes"` (city-specific openness collapsed, LOC-2)
    and/or when `location == "Remote (India)"` (no base city, LOC-3).
- `parse_date(raw) -> date | None` (deterministic formats only; ISO + the snapshot/data format)
- `coerce_confidence(raw) -> Confidence` (default low + warning on unknown)

> **Verified real supply headers (2026-06-19):**
> `Beach.csv`: `#, Name, Email, Grade, Key Skills, Location, Chennai-open, Days on Beach, Notes`.
> `Rolling Off.csv`: `…, Current Client, Roll-off Date, Confidence, Location, Chennai-open, Notes`.
> `New Joiners.csv`: `…, Key Skills (from CV), Join Date, Location, Chennai-open, Notes`.
> `Location` + `Chennai-open` are **separate columns** (earlier drafts assumed one `<City>-open`
> cell — corrected). New-joiner skills come from `Key Skills (from CV)` → flagged `unverified` (TX-3).

Column-name lookup is case/whitespace-insensitive (mirrors a-001 classify) so real headers
(`Roll-off Date`, `Join Date`, `Grade`, `Location`, `Chennai-open`, `Confidence`,
`Key Skills (from CV)`) resolve. Date/format constants
and the column-alias map live in `config/` (tech.md rule 6) where they are more than trivial.

## taxonomy.py — design

```python
class Taxonomy:
    def canonical_skill(self, raw: str) -> tuple[str, bool]:   # (name, unmapped)
    # title/domain maps seeded but minimal this slice (they arrive via resume enrich, out of scope)
```
- Canonical skill aliases loaded from `config/taxonomy.yaml` via the `dsm/config.py` cached loader
  (no new dependency). Lookup is normalized (lowercase, trim). Hit → `(canonical, False)`; miss →
  `(raw_verbatim, True)` and the surface form is **queued**: `lineage.log_unmapped_skill(...)` +
  counted (TX-2). The queue is a lineage/metrics seed (a list + structlog), not new persistence;
  taxonomy ownership of alias additions is §15#6 (deferred).

## Silver persistence + CLI wiring

### `write_normalized` (silver-layer writer, mirrors bronze `write_records`)
```python
def write_normalized(records: list[NormalizedRecord], source_hash: str, silver_root: Path) -> Path
```
- Writes to `data/silver/records/<hex>.jsonl` (the `"sha256:"` prefix stripped for the filename),
  one `record.model_dump_json()` per line, **atomic temp+rename** — byte-for-byte the bronze pattern
  in `dsm/ingest/blobstore.py::write_records`. Content-addressed by the **source** blob hash; rewrite
  of the same source is idempotent (SW-1). Immutable layer (AD-066/§4).
- `.gitignore`: add `data/silver/records/*` + a `.gitkeep` keep-rule, matching the bronze stanza
  (SW-2 — `raw_text` is PII-dense).
- Storage layout note: `data/silver/` already appears in `docs/structure.md`; this slice realizes
  the `records/` subtree under it.

### `dsm ingest` wiring (Lane C `dsm/cli/commands.py`, after the existing Parse loop)
For each LANDED, parseable source, the command already parses to bronze and calls `write_records`.
Silver hooks in **right after**: it normalizes that source's bronze records and persists them.
```python
silver_dir: Annotated[Path, typer.Option("--silver-dir")] = _SILVER_DEFAULT   # data/silver
# ... after `write_records(records, entry.raw_bytes_hash, bronze_dir)`:
normalized = normalize_run(records, manifest, taxonomy=taxonomy, run_id=rid)   # snapshot_date via manifest
write_normalized(normalized, entry.raw_bytes_hash, silver_dir)
# accumulate summary counters (no PII)
```
- **Summary (CLI-1):** a `── Silver ──` block — `normalized: N`, `coercion-skipped: M`, availability
  split (`free_now`/`rolling_off`/`new_joiner`), `unmapped skills: U`, `records w/ warnings: W`.
- **PII safety (CLI-2):** the only per-record line allowed is
  `cid=<candidate_id> <source_type> avail=<type> skills=<n> warn=<n>` — **never** `raw_text`, name,
  or email. The summary counters are derived from `NormalizedRecord` fields, not free text.
- **Exit semantics (CLI-3):** coercion skips are counted, **not** a failure exit (expected invalid
  data, logged via `lineage`); only unexpected exceptions exit non-zero — identical to today's
  land/parse handling. `DSM_CANDIDATE_ID_KEY` must be set in the environment for `dsm ingest` to run
  silver (fail fast with a clear message if absent — it gates `candidate_id`).
- **Lane note:** this edits Lane C's `dsm/cli/commands.py`. a-001 already added the `ingest` command
  here (Lane A), so extending its body is consistent precedent; flag at PR for Lane C awareness.

## lineage.py — extend
Reuse a-001's `log_invalid`. Add `log_unmapped_skill(*, run_id, surface_form, candidate_id)` and a
per-run unmapped-skill count (metrics seed, §12). Determinism: counts derive from the record/skill
stream, not a global mutable counter.

## Dependencies, config, contracts
- **No new runtime dependency.** HMAC via stdlib `hmac`/`hashlib`. `config/taxonomy.yaml` read via
  the existing PyYAML loader (AD-064). `Grade`/`Confidence`/`NormalizedSkill`/`NormalizedRecord`
  extend the existing `dsm/ingest/models.py`.
- **Import contract (REVISED — option b):** NF-IMPORT-1 is narrowed so `dsm.ingest → dsm.pii.vault`
  is permitted while every other `dsm.pii` submodule stays forbidden to ingest (restructured as
  described in "Identity & vault placement"). Edited in its own commit with a note → ADR-076. This
  is a deliberate, signed-off PII-boundary relax (tech.md rule 1 / CLAUDE.md STOP trigger), not a
  silent divergence.

## Eval / unit cases to add (deterministic, no network/LLM)

| Case | Fixture | Criterion |
| --- | --- | --- |
| beach → FreeNow | beach row | AV-1 |
| rolling_off → RollingOff{date,confidence} | rolling_off row | AV-2 |
| new_joiners → NewJoiner{join_date} | new-joiner row | AV-3 |
| missing/invalid date → skip | rolling_off w/ bad date | AV-4 + NF-3 (logged+counted) |
| `candidate_id` stable + deterministic | same email twice | ID-1, ID-2 |
| `candidate_id` collision-safe | two "Aarav", different emails | ID-3 (different ids, not merged) |
| no email leak | any supply row | ID-5 (`NormalizedRecord` has no email) |
| unmapped skill flagged + queued | skill not in taxonomy | TX-2 (`unmapped=True` + logged + counted) |
| new-joiner skills unverified | new-joiner w/ skills | TX-3 (AD-032) |
| Chennai-open=Yes → remote_eligible + warning | `Location=Bengaluru`, `Chennai-open=Yes` | LOC-2 |
| Chennai-open=No → not remote (unless Remote India) | `Location=Pune`, `Chennai-open=No` | LOC-2 / LOC-NET |
| Remote (India) → remote_eligible + city None + warning | `Location=Remote (India)` | LOC-3 (city=None, AD-075) |
| missing grade/location → None + warning, still emitted | sparse row | GR-1, LOC-4 |
| two sheets, one snapshot → 2 records, same id, not merged | same email in beach + rolling_off | NF-4 |
| `valid_as_of` from banner | supply record + manifest snapshot_date | VAOF-1 |
| determinism | same input twice | NF-2 (identical output) |
| silver write round-trip | normalize → `write_normalized` → read back | SW-1 (`data/silver/records/<hash>.jsonl`, line count, atomic, idempotent re-write) |
| CLI summary is PII-safe | run `ingest` over a fixture raw dir | CLI-1/CLI-2 (summary counts correct; **no `raw_text`/name/email** in stdout) |
| CLI coercion skips don't fail | raw dir w/ one bad-date row | CLI-3 (counted, exit 0) |

Fixtures live under `tests/fixtures/ingest/silver/` (synthetic, committed). The `DSM_CANDIDATE_ID_KEY`
is set to a fixed test key in the test harness so `candidate_id` assertions are stable.

## Edge cases
- Same email on two `BronzeRecord`s with different supply `source_type` (e.g. `supply_beach` +
  `supply_rolling_off`) in one snapshot → two `NormalizedRecord`s, one `candidate_id`, **no merge**
  (NF-4). (Silver reads the sheet-of-origin from each `BronzeRecord`'s `source_type`/`source_hash`,
  which a-001 parsing preserved; merge across them is the gold stage.)
- Colliding first names, different emails → different `candidate_id`s (ID-3).
- Unmapped skill → emitted `unmapped=True` + queued; never dropped (TX-2).
- Missing/invalid discriminator date → record skipped + counted (AV-4); missing grade/location →
  emitted with `None` + warning (LOC-4).
- Missing HMAC key → fail fast (no silent default key — that would make ids non-reproducible/insecure).
- Empty skills list → valid `NormalizedRecord`.
