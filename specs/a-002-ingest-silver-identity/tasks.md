# Tasks — a-002 Ingest Silver & Identity Resolution

> Ordered, atomic, independently testable. **One task = one commit**, imperative, referencing the
> spec/ADR. `make check` green before every commit. Each task maps to ≥1 acceptance criterion.
> **Tasks T-000a/T-000b are GATES: code below them is blocked until the two sign-off items in
> `requirements.md` are resolved.**

## Decisions resolved (2026-06-19) — still need whole-spec sign-off before code

- **D-1 (was item 1) → option (A):** relax `Location.city → str | None` (ADR-075). `NormalizedSkill`/
  `Grade`/`Confidence` are ingest-local; other frozen types reused unchanged.
- **D-2 (was item 2) → option (b):** canonical `dsm/pii/vault.py` (Lane C dir) + narrow NF-IMPORT-1
  relax (`dsm.ingest → dsm.pii.vault` only). Recorded as ADR-076.
- **Coordination → RESOLVED (2026-06-19):** **Lane A seeds** a minimal `dsm/pii/vault.py`
  (derivation + `Vault` protocol + in-memory store); Lane C hardens the encrypted at-rest store
  later. ADR-076 must record Lane C's agreement to the placement + seeding.

## Implementation (after whole-spec sign-off)

- **T-000-ADR — Record ADRs** → append **AD-075** (Location.city optional) and **AD-076**
  (candidate_id in `dsm/pii/vault.py`; narrowed ingest→pii.vault import; Lane C agreement noted) to
  `docs/decision.md`. _(AD-060 process; precedes the edits they authorize.)_

- **T-001-FROZEN — Relax `Location.city`** → edit `dsm/models.py`: `city: str | None = None`. Own
  commit (`refactor(models): relax Location.city to optional per AD-075`). Verify `make check` (no
  pyright/test regressions in gates/match/cli). _(AD-075; D-1)_

- **T-001 — Identity derivation + Vault protocol (CROSS-LANE — Lane C dir)** → `dsm/pii/vault.py`:
  `candidate_id(email)` = HMAC-SHA256 over normalized email, key from `DSM_CANDIDATE_ID_KEY` (fail
  fast if absent), `"cid:"` prefix; declare the `Vault` protocol + minimal in-memory store (no
  encryption — deferred to Lane C). Tests: ID-1, ID-2, ID-3, ID-5; missing-key fail-fast. _(AD-067;
  AD-076; requires the coordination nod above.)_

- **T-001-IMPORT — Narrow NF-IMPORT-1** → `pyproject.toml`: restructure the ingest-forbidden
  contract so `dsm.ingest → dsm.pii.vault` is allowed while `dsm.pii.redact`/`dsm.pii.leakscan`/
  `dsm.pii.pseudonymised_lm` stay forbidden. Own commit with a note → AD-076. Verify import-linter
  green. _(NF-IMPORT-1 revised; D-2)_

- **T-002 — Ingest-local silver models** → extend `dsm/ingest/models.py` with `Grade`, `Confidence`,
  `NormalizedSkill`, `NormalizedRecord`; import frozen `Location`/`AvailabilityState`/`FreeNow`/
  `RollingOff`/`NewJoiner`/`ProficiencyLevel` from `dsm.models`. Tests: instantiation + frozen +
  discriminated-union round-trip. _(§6 Phase 3; TX-4; reflects D-1)_

- **T-003 — Taxonomy + config** → `dsm/ingest/taxonomy.py` + `config/taxonomy.yaml` (seed aliases).
  `Taxonomy.canonical_skill(raw) -> (name, unmapped)` via the cached YAML loader. Tests: known alias
  → canonical/`unmapped=False`; unknown → verbatim/`unmapped=True`. _(TX-1, TX-2)_

- **T-004 — lineage: unmapped-skill queue** → extend `dsm/ingest/lineage.py` with
  `log_unmapped_skill` + per-run count (metrics seed). Tests: logged + counted, deterministic. _(TX-2, §12)_

- **T-005 — Normalization helpers** → `dsm/ingest/silver.py`: `parse_grade`,
  `parse_location(location, chennai_open)` (reads **both** columns — LOC-1/2/3/NET), `parse_date`,
  `coerce_confidence`. Pure, no I/O. Tests: `Chennai-open=Yes`→remote+warning (LOC-2),
  `Chennai-open=No`→not-remote (LOC-NET), `Remote (India)`→remote+city=None+warning (LOC-3), plain
  city (LOC-1), bad/missing grade→None+warning (GR-1), date coercion. _(GR-1, LOC-1..NET)_

- **T-006 — Per-sheet availability + `normalize`** → wire helpers + `dsm.pii.vault.candidate_id` +
  taxonomy into `normalize(record, *, snapshot_date, taxonomy, run_id) -> NormalizedRecord | None`.
  Sheet→availability (AV-1/2/3); fatal coercion (no email / no discriminator date) → log+skip+count
  → `None` (AV-4, ID-4, NF-3); new-joiner skills `unverified=True` (TX-3); resume/feedback →
  `raw_text` + `availability=None` (AV-5). Tests: AV-1/2/3/4/5, TX-3, ID-4. _(maps each sheet type)_

- **T-007 — `normalize_run` + `valid_as_of` threading** → build `source_hash → snapshot_date` map
  from the a-001 manifest; map `normalize` over records in sorted order; stamp `valid_as_of`
  (VAOF-1) and `extractor_version` (NF-1). Tests: VAOF-1; NF-2 determinism (same input → identical
  output); NF-4 (two sheets, one snapshot → 2 records, same `candidate_id`, not merged). _(NF-1/2/4, VAOF-1)_

- **T-008 — Golden silver fixtures + end-to-end test** → `tests/fixtures/ingest/silver/`; drive
  bronze→silver over a small synthetic snapshot; assert the full case table in `design.md`. Fixed
  `DSM_CANDIDATE_ID_KEY` in the harness. No network/LLM. _(end-to-end acceptance)_

- **T-008-WRITE — Silver-layer writer + gitignore** → `dsm/ingest/silver.py::write_normalized`
  (`data/silver/records/<hash>.jsonl`, atomic temp+rename, idempotent — mirrors bronze
  `write_records`); add `data/silver/records/*` (+ `.gitkeep`) to `.gitignore`. Tests: write
  round-trip, line count, idempotent re-write. _(SW-1, SW-2)_

- **T-008-CLI — Wire silver into `dsm ingest`** → edit `dsm/cli/commands.py` (Lane C): add
  `--silver-dir`; after the parse loop, run `normalize_run` + `write_normalized` per source; print
  the `── Silver ──` summary (totals, coercion-skipped, availability split, unmapped-skill count,
  records-with-warnings). **No `raw_text`/PII to stdout**; coercion skips counted, not exit-1; fail
  fast if `DSM_CANDIDATE_ID_KEY` unset. Tests: summary counts; **assert no `raw_text`/name/email in
  stdout**; bad-date row → counted + exit 0. _(CLI-1/2/3)_

- **T-009 — Verify import contract + docs refresh** → confirm the revised NF-IMPORT-1 is green
  (`dsm.ingest → dsm.pii.vault` allowed; other `dsm.pii` submodules still forbidden); refresh
  `docs/progress.A.md` via `/handoff`; refresh any spec line that drifted in the same PR.
  _(NF-IMPORT-1 revised; CLAUDE.md refresh rule)_

## Definition of done
All acceptance criteria in `requirements.md` met · `make check` green · each new behaviour has a
test · new decisions (AD-075 if chosen, AD-076) appended to `docs/decision.md` · `docs/progress.A.md`
updated via `/handoff`. Vault **write path**, enrich, merge/gold, reconcile, embedding remain out of
scope (later slices).
