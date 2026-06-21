# B-001 Query-Time Deterministic Foundation — Tasks

> **Lane:** B · **Slice:** B-1  
> One task = one commit. `make check` GREEN after each.  

---

## T-000 · ADR sign-off gate

Ratify AD-086 (Location split), AD-087 (freshness guard), AD-088
(`ExclusionReason.HARD_SKILL_MISMATCH`) in `docs/decision.md`. All three are
prerequisites for dependent code. AD-086 and AD-088 are frozen-contract
amendments (AD-060) — **STOP for human sign-off before proceeding.**

**Acceptance:** AD-086/087/088 recorded in `docs/decision.md` with correct
next IDs, cross-referencing the architecture and this spec.

---

## T-001 · Amend frozen contracts (`dsm/models.py`)

Apply the two frozen-contract amendments ratified in T-000:

1. `Location`: replace `remote_eligible: bool = False` with
   `remote_within_country: bool = False` + `onsite_cities: frozenset[str] = frozenset()`.
2. `ExclusionReason`: add `HARD_SKILL_MISMATCH = "hard_skill_mismatch"`.

Update `tests/test_models.py` for the `Location` default assertion.

**Acceptance:** `make check` GREEN. `Location` and `ExclusionReason` match the
ratified AD-086/088 shapes.

---

## T-002 · Migrate ingest silver (`dsm/ingest/silver.py`)

Update `parse_location` to produce the AD-086 model:

- `Chennai-open=Yes` → `onsite_cities=frozenset({"Chennai"})` (keep home `city`)
- `"Remote (India)"` → `remote_within_country=True, city=None`
- Plain city → both defaults

Update tests:
- `tests/ingest/test_silver_helpers.py`
- `tests/ingest/test_silver_normalize.py`
- `tests/ingest/test_silver_e2e.py`
- `tests/ingest/test_models.py`

**Acceptance:** `make check` GREEN. All existing silver tests pass with new
field names; no `remote_eligible` references remain in `dsm/ingest/`.

---

## T-003 · Migrate index models (`dsm/index/models.py`)

Update `CandidateIndexRecord`, `FilterFields`, `project_filter_fields`,
`build_record`:

- Replace `remote_eligible: bool` with `remote_within_country: bool` +
  `onsite_cities: list[str]` (Milvus-compatible, not `frozenset`).
- `project_filter_fields`: map from `Location` new fields.
- `build_record`: use new fields.

Update `tests/index/test_index_models.py`.

**Acceptance:** `make check` GREEN. Index model tests pass with new fields.

---

## T-004 · Migrate Milvus store schema (`dsm/index/milvus_store.py`)

Update collection schema:

- Remove `add_field("remote_eligible", DataType.BOOL)`.
- Add `add_field("remote_within_country", DataType.BOOL)`.
- Add `add_field("onsite_cities", DataType.ARRAY, element_type=DataType.VARCHAR,
  max_capacity=16, max_length=128)`.
- Update insert row dict to use new field names.

Update `tests/index/test_milvus_store.py`.

No prod data migration — `ensure_collection` rebuilds the test `.db`.

**Acceptance:** `make check` GREEN. Milvus store tests pass with new schema.

---

## T-005 · Migrate fixtures + near-miss builder

Update `tests/fixtures/__init__.py`:

- `_candidate()` helper: replace `remote_eligible` param with
  `remote_within_country` + `onsite_cities`.
- `role_02()`: update fixture candidates to exercise `onsite_cities`
  (Priya: `onsite_cities={"Chennai"}` instead of `remote_eligible=True`).

Update `dsm/cli/commands.py::build_near_misses`:

- Reframe location-miss `gap_summary` wording (no more "not open to
  relocation"; use "city not in role's onsite set" or equivalent).

**Acceptance:** `make check` GREEN. All fixture-dependent tests pass.
No `remote_eligible` references remain anywhere in the codebase.

---

## T-006 · Rewrite location gate (`dsm/match/gates.py`)

Rewrite `_location_passes` to AD-086 semantics:

- Onsite: pass iff `role.city is not None and (city match OR role.city in
  onsite_cities)` (case-insensitive). `remote_within_country` never clears.
- Distributed: pass iff `candidate.country == role.country`.

Update `tests/match/test_gates.py`:

- Update existing location tests for new semantics.
- Add: `onsite_cities` membership test, `remote_within_country` does-not-clear
  onsite test, distributed same-country test.

Keep `filter_candidates` structure unchanged (location first, availability
second, G-OUT-2 short-circuit).

**Acceptance:** `make check` GREEN. All gate ACs from `requirements.md` FR-3
pass. `gates.py` imports only `dsm.models` + stdlib.

---

## T-007 · Query-time models (`dsm/match/models.py`)

Create `dsm/match/models.py` with:

- `OpenRolesBanner(demand_as_of: date, source_path: str)`
- `DemandParseOutcome(banner: OpenRolesBanner, roles: list[OpenRole], skipped: list[str])`

Reuse frozen `SkillDepth`, `SkillRequirement`, `OpenRole` from `dsm/models.py`.
Do not redefine.

**Acceptance:** `make check` GREEN. Models importable and frozen.

---

## T-008 · Freshness guard (`dsm/match/freshness.py`)

Create `dsm/match/freshness.py` with:

- `FreshnessVerdict(action: str, staleness_days: int, message: str)` model
- `check_freshness(demand_as_of, valid_as_of, start_date, max_staleness_days)
  → FreshnessVerdict`

Pure datetime arithmetic. Four pinned cases (FR-2).

Create `tests/match/test_freshness.py` with tests for all four decision-tree
branches.

**Acceptance:** `make check` GREEN. All FR-2 ACs pass.

---

## T-009 · Demand CSV parser (`dsm/match/demand.py`)

Create `dsm/match/demand.py` with `parse_demand(csv_path) → DemandParseOutcome`.

Parsing rules:
- Banner `demand_as_of` extraction
- Skill split: `(expert|advanced|intermediate|beginner)` → HARD;
  `(nice to have)` or bare → DESIRED
- `Co-location` → `co_location_required`
- `Notes / Constraints` → `OpenRole.description`
- `Location` → AD-086 model
- `Priority` → batch sort order
- Log+skip malformed rows; missing banner blocks

Create `tests/match/test_demand.py` with tests for:
- Banner parse
- Skill split (HARD/DESIRED)
- Co-location mapping
- `Notes` → `description`
- `Priority` batch order
- Malformed rows logged+skipped+counted
- Missing banner blocks

**Acceptance:** `make check` GREEN. All FR-1 ACs pass.

---

## T-010 · Exact hard-skill filter (`dsm/index/retrieve.py`)

Create `dsm/index/retrieve.py` with `exact_hard_skill_filter(pool, hard_skills)
→ (filtered_pool, exclusions)`.

- `skill_set` set membership + `ProficiencyLevel` ordinal floor
- Excluded candidates → `Exclusion(reason=HARD_SKILL_MISMATCH, …)`
- Deterministic, LLM-free

Create `tests/index/test_retrieve.py` with tests for:
- Exact filter: `skill_set` membership + proficiency floor
- `HARD_SKILL_MISMATCH` exclusion logged
- Empty pool → exclusions only

**Acceptance:** `make check` GREEN. All FR-4 ACs pass.

---

## T-011 · Verify rank + final check

Verify `dsm/match/rank.py`:
- Confirm sort order matches §6.10 (`combined_score` desc →
  `hard_skill_coverage` desc → `desired_skill_coverage` desc →
  `email` asc)
- Run `tests/match/test_rank.py` — all pass, no change needed

Run full `make check`. Confirm no `remote_eligible` references remain.
Update ADR refs in `rank.py` docstring if needed.

**Acceptance:** `make check` GREEN. All acceptance criteria from
`requirements.md` met. No regressions.
