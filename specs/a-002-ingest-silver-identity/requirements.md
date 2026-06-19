# Requirements — a-002 Ingest Silver & Identity Resolution

> Slice 2 of ingestion: deterministic `BronzeRecord → NormalizedRecord`. Typed, normalized,
> identity-resolved. **No LLM, no network.** Builds on a-001 (bronze + `BronzeRecord`).
> References: `ee-ingestion-architecture.md` §3/§5(step 3)/§6(Phase 3)/§13/§15#3;
> AD-012/AD-032/AD-060/AD-062/AD-063(a)/AD-065/AD-066/AD-067/AD-068/AD-070; `docs/structure.md`;
> `docs/ownership.md`; `docs/tech.md` (determinism, PII boundary).

## User story

As the staffing engine, given the bronze records produced by a-001, I need each row/item turned
into a **typed, normalized, identity-resolved `NormalizedRecord`** keyed by a stable
`candidate_id`, so that downstream enrich/merge/gold can build one canonical `Candidate` per
consultant — deterministically, with no PII leaving the vault boundary and no LLM deciding
anything.

## Product invariants this slice must uphold

- **Deterministic, LLM-free** (AD-002 spirit; §5 step 3 has no LLM). Same bronze in → same silver out.
- **Email is the join key, tokenized to `candidate_id = HMAC(email)`** (AD-012/AD-067). Name is
  **never** a join key (colliding first names).
- **No raw PII past the vault boundary** (AD-068, tech.md rule 1): `NormalizedRecord` carries
  `candidate_id`, never raw email/name.
- **Invalid records logged + skipped + counted, never silently passed** (§10/§12).
- **Replay from bronze; derivations are versioned** — `extractor_version` stamped (AD-066/§11).
- **Frozen contract is law** (AD-060): reuse `dsm/models.py` types; any edit needs sign-off + a
  superseding ADR (see §"Sign-off items").

## Acceptance criteria (EARS)

### Identity (`candidate_id`)
- **ID-1** — WHEN a bronze record carries an email (supply row, resume `email_found`, feedback
  `email_key`), the system SHALL derive `candidate_id = HMAC(key, normalized_email)` and place it
  on the `NormalizedRecord`.
- **ID-2** — WHEN the same email appears in two different snapshots or runs, the system SHALL
  produce the **identical** `candidate_id` (stable + deterministic).
- **ID-3** — WHEN two consultants share a first name but have different emails, the system SHALL
  produce **different** `candidate_id`s and SHALL NOT merge them (join only on email/`candidate_id`).
- **ID-4** — WHEN a record's email is missing or unparseable, the system SHALL log+skip+count it as
  invalid (no `candidate_id` can be derived) — except resume/feedback, where a missing email key is
  already handled upstream in a-001.
- **ID-5** — The raw email SHALL NOT appear on the `NormalizedRecord` nor in any silver output.

### Per-sheet availability mapping
- **AV-1** — WHEN `source_type = supply_beach`, the system SHALL set `availability = FreeNow`.
- **AV-2** — WHEN `source_type = supply_rolling_off`, the system SHALL set
  `availability = RollingOff{expected_date, confidence}` from the roll-off date + confidence columns.
- **AV-3** — WHEN `source_type = supply_new_joiners`, the system SHALL set
  `availability = NewJoiner{join_date}` from the join-date column.
- **AV-4** — WHEN a supply row's discriminating date (`expected_date`/`join_date`) is missing or
  unparseable, the system SHALL log+skip+count the record (the availability variant cannot be built).
- **AV-5** — WHEN `source_type ∈ {resume, feedback}`, the system SHALL set `availability = None` and
  populate `raw_text` for the later enrich phase.

### Normalization (grade, location, dates)
- **GR-1** — WHEN a supply row carries a recognized grade, the system SHALL map it to the `Grade`
  enum; WHEN missing/unrecognized, the system SHALL set `grade = None` and append a `parse_warning`.
  > **Real supply schema (verified against `data/raw/supply/*.csv`, 2026-06-19):** `Location` and
  > `Chennai-open` are **two separate columns**. `Location` holds a plain city
  > (`Chennai`/`Bengaluru`/`Delhi NCR`/`Pune`/`Hyderabad`) **or** `Remote (India)`; `Chennai-open`
  > is a separate `Yes`/`No` flag. (Earlier drafts wrongly assumed a single `Location` cell with a
  > `<City>-open` value.)
- **LOC-1** — WHEN the `Location` column names a city, the system SHALL set `location.city` to it.
- **LOC-2** — WHEN the separate `Chennai-open` column is `Yes`, the system SHALL set
  `remote_eligible = True` (independent of the base city) and append a `parse_warning` recording
  that city-specific openness is collapsed into the boolean (AD-063(a); §15#3 overloading **noted,
  not modelled**). WHEN `No`/absent, the `Chennai-open` column alone SHALL NOT set `remote_eligible`.
- **LOC-3** — WHEN the `Location` column is `Remote (India)`, the system SHALL set
  `remote_eligible = True` and leave `location.city = None` (AD-075), appending a `parse_warning`.
- **LOC-NET** — Net rule: `remote_eligible = (Location == "Remote (India)") OR (Chennai-open == "Yes")`;
  `city = Location` (or `None` for `Remote (India)`).
- **LOC-4** — WHEN grade/location is missing, the record SHALL still be emitted (those fields are
  nullable); only a missing identity (ID-4) or a missing availability discriminator (AV-4) is fatal.
- **VAOF-1** — WHEN a bronze record originates from a supply snapshot whose banner carried an
  `as of <date>`, the system SHALL stamp `valid_as_of` from that snapshot date (sourced from the
  a-001 manifest, keyed by `source_hash`); else `valid_as_of = None`.

### Skills + taxonomy
- **TX-1** — WHEN a raw skill maps to a taxonomy canonical id, the system SHALL emit it with the
  canonical name and `unmapped = False`.
- **TX-2** — WHEN a raw skill has no taxonomy match, the system SHALL emit it with `unmapped = True`,
  keep the verbatim surface form as the name, and **queue** it (logged + counted) for review (§15#6).
- **TX-3** — WHEN skills come from `supply_new_joiners`, the system SHALL flag every emitted skill
  `unverified = True` (AD-032: counted, never penalized; the human sees the uncertainty).
- **TX-4** — Skill proficiency MAY be absent at silver (CV-derived / supply rows carry none);
  proficiency SHALL be optional and reuse the frozen `ProficiencyLevel` enum.

### Silver persistence & CLI output
- **SW-1** — WHEN silver normalizes the bronze records for a source blob, the system SHALL persist
  the resulting `NormalizedRecord`s to `data/silver/records/<source_hash>.jsonl` (one
  `model_dump_json()` per line), immutable + content-addressed (AD-066/§4), via atomic temp+rename
  (mirrors bronze `write_records`).
- **SW-2** — The silver layer is **PII-dense** (`raw_text` carries resume/feedback bodies) and SHALL
  be gitignored exactly like bronze (a-001 L-LAYOUT-3).
- **CLI-1** — WHEN `dsm ingest` runs, after the parse step it SHALL run silver over the parsed
  bronze records and persist them (SW-1), then print a **Silver summary**: total normalized,
  coercion-skipped count, per-availability-type counts (free_now / rolling_off / new_joiner),
  unmapped-skill count, and records-with-warnings count.
- **CLI-2** — The console output SHALL NOT print `raw_text` or any PII. Any per-record line SHALL
  show only `candidate_id`, `source_type`, availability type, `#skills`, and `#warnings` (PII-safe).
- **CLI-3** — Coercion-skipped rows (AV-4/ID-4) are **counted in the summary, not a failure exit**
  (they are expected invalid data, logged via lineage). Only unexpected exceptions SHALL exit
  non-zero, consistent with the existing land/parse behaviour.

### Determinism, versioning, observability
- **NF-1** — `NormalizedRecord.extractor_version` SHALL be stamped with the pinned silver derivation
  version (a model/logic bump is a version bump — AD-066/§11).
- **NF-2** — The same `(BronzeRecord, snapshot_date, taxonomy version, extractor_version)` input
  SHALL always produce byte-identical `NormalizedRecord` output (no clocks, no RNG, sorted iteration).
- **NF-3** — Every coercion/validation failure SHALL be logged via `lineage` (reason + payload +
  run_id) and counted; none silently swallowed (§10/§12).
- **NF-4** — A consultant appearing on two supply sheets in one snapshot SHALL yield **two**
  `NormalizedRecord`s sharing one `candidate_id` (silver is per-source; **merge is a later slice**).
- **NF-IMPORT-1 (REVISED — sign-off item 2 chose option b)** — `dsm.ingest` SHALL NOT import
  `dsm.match`, `dsm.index`, `dspy`, `modal`, or `httpx`. The forbidden-modules list is **narrowed**
  to permit `dsm.ingest → dsm.pii.vault` **only** (the identity/vault store); all other `dsm.pii`
  submodules (redact, leakscan, PseudonymisedLM) remain forbidden to ingest. The relax is a single
  contract edit in its own commit with a note (AD-076).

## Out of scope (do not build here)
LLM enrich; PII redaction / NER / leak-scan of free text; merge / gold / `Candidate`; snapshot
reconcile + tombstones; embedding/index. The **encrypted identity-vault write path** is out of scope
(no `vault_ref`s are needed until gold) — only the `candidate_id` derivation is in scope. **Note:**
the **silver-layer** write path (`data/silver/`, SW-1) **is now in scope** (it is the observable
output of this stage) — distinct from the identity-vault write path, which stays out. See §13/§15.

## Sign-off items — RESOLVED (decisions recorded 2026-06-19)
1. **Frozen-model edit (`dsm/models.py`)** — **RESOLVED → option (A): relax `Location.city: str →
   str | None = None` via new superseding ADR-075.** `NormalizedSkill`/`Grade`/`Confidence` stay
   ingest-local; frozen `ProficiencyLevel`/`FreeNow`/`RollingOff`/`NewJoiner`/`AvailabilityState`
   are reused unchanged. The `Location.city` edit is a standalone commit (T-001-FROZEN).
2. **Vault placement / import contract** — **RESOLVED → option (b): canonical `dsm/pii/vault.py`
   (Lane C-owned per AD-062) + a narrow NF-IMPORT-1 relax** (`dsm.ingest → dsm.pii.vault` only).
   Recorded as ADR-076. **One open coordination point (confirm at spec sign-off):** `dsm/pii/` is
   **Lane C** — see `design.md §"Identity & vault placement"` for who builds `vault.py` and the
   blocker this creates for Lane A.

> Spec sign-off itself (this whole document) is still required before code per CLAUDE.md golden
> rule 1; the two decisions above are settled, the spec is not yet approved.
