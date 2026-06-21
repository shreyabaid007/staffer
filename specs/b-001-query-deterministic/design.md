# B-001 Query-Time Deterministic Foundation — Design

> **Lane:** B · **Slice:** B-1  
> **Architecture ref:** `ee-query-architecture.md` §5, §6.0–6.5, §6.10, §13  

---

## 1. Data-flow diagram

```
Open Roles CSV
      │
      ▼
┌─────────────────────────────┐
│ 1  Parse demand             │  dsm/match/demand.py
│    banner → demand_as_of    │  → DemandParseOutcome
│    rows → list[OpenRole]    │    (banner + roles + skipped)
│    Notes → description      │
│    Skills → HARD / DESIRED  │
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│ 3  Freshness guard          │  dsm/match/freshness.py
│    demand_as_of vs          │  → FreshnessVerdict
│    supply valid_as_of       │    (ok / warn / refuse)
│    REFUSE → block run       │
└─────────┬───────────────────┘
          │ ok / warn
          ▼
┌─────────────────────────────┐
│ 4  Gate pre-filter          │  dsm/match/gates.py
│    location (AD-086) first  │  → (EligiblePool, ExclusionLog)
│    availability second      │
│    G-OUT-2 short-circuit    │
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│ 5  Exact hard-skill filter  │  dsm/index/retrieve.py
│    skill_set ∩ + prof floor │  → (filtered EligiblePool,
│    HARD_SKILL_MISMATCH      │     exclusions)
│    No adjacency             │
└─────────┬───────────────────┘
          │
          ▼
     [B-2: recall → rerank → score → rank]
```

---

## 2. ADRs to ratify (`T-000-ADR` — gate task)

### AD-086 — Split `Location.remote_eligible` into `remote_within_country` + `onsite_cities`

**Status:** Proposed (frozen-contract amendment, AD-060). Cross-lane (touches Lane A index
records + Lane C gates). Supersedes AD-063a location-gate semantics.

**Decision:** Replace `Location.remote_eligible: bool` with:

```python
class Location(BaseModel, frozen=True):
    city: str | None = None
    state: str | None = None
    country: str = "India"
    remote_within_country: bool = False
    onsite_cities: frozenset[str] = frozenset()
```

**Gate semantics:**
- Onsite (`co_location_required=True`): pass iff `role.city is not None and
  (candidate.city == role.city OR role.city in candidate.onsite_cities)` (case-insensitive).
  `remote_within_country` never clears an onsite gate.
- Distributed (`co_location_required=False`): pass iff `candidate.country == role.country`.

**Why:** The overloaded `remote_eligible` cannot distinguish "works remote from Pune" from
"will go onsite in Chennai" — a load-bearing invariant for ROLE-02.

### AD-087 — Query-time as-of freshness guard

**Status:** Proposed. Touches no frozen model; reuses `config.reconcile.max_staleness_days`.

**Decision:** Compare `demand_as_of` vs supply `valid_as_of`:
- `staleness_days ≤ max_staleness_days` → `ok`
- `0 < staleness_days ≤ max` AND `start_date < valid_as_of` → `warn`
- `staleness_days > max_staleness_days` → `refuse` (block run)

### AD-088 — Add `ExclusionReason.HARD_SKILL_MISMATCH`

**Status:** Resolved, pending sign-off (frozen-contract amendment, AD-060).

**Decision:** Add `HARD_SKILL_MISMATCH` to `ExclusionReason` so the exact filter can explain
hard-skill-gap exclusions. Near-miss ordering: hard-skill-gap misses rank below availability
misses (availability is actionable; hard-skill gaps are structural).

---

## 3. Modules touched

### 3.1 `dsm/models.py` — AMEND (frozen contract)

**Location** — replace `remote_eligible: bool = False` with:

```python
remote_within_country: bool = False
onsite_cities: frozenset[str] = frozenset()
```

**ExclusionReason** — add:

```python
HARD_SKILL_MISMATCH = "hard_skill_mismatch"
```

### 3.2 `dsm/match/models.py` — NEW

New query-time intermediates only. **REUSE** frozen `SkillDepth` + `SkillRequirement` from
`dsm/models.py` (do not redefine).

```python
class OpenRolesBanner(BaseModel, frozen=True):
    demand_as_of: date
    source_path: str

class DemandParseOutcome(BaseModel, frozen=True):
    banner: OpenRolesBanner
    roles: list[OpenRole]
    skipped: list[str] = Field(default_factory=list)
```

### 3.3 `dsm/match/freshness.py` — NEW

```python
class FreshnessVerdict(BaseModel, frozen=True):
    action: str          # "ok" | "warn" | "refuse"
    staleness_days: int
    message: str

def check_freshness(
    demand_as_of: date,
    valid_as_of: date,
    start_date: date,
    max_staleness_days: int = 30,
) -> FreshnessVerdict: ...
```

Pure datetime arithmetic. No LLM, no config import (caller supplies `max_staleness_days`).

### 3.4 `dsm/match/demand.py` — NEW

```python
def parse_demand(csv_path: Path) -> DemandParseOutcome: ...
```

Parses the Open Roles CSV:
1. Extract banner `demand_as_of` from header line
2. For each row: split `Required Skills` on `;`, classify HARD vs DESIRED, map columns
3. Sort by `Priority`, build `OpenRole`s
4. Log+skip invalid rows; missing banner → raise `ValueError`

**Parsing rules:**
- `"<skill> (expert|advanced|intermediate|beginner)"` → `SkillRequirement(depth=HARD, min_proficiency=<level>)`
- `"<skill> (nice to have)"` or bare `"<skill>"` → `SkillRequirement(depth=DESIRED, min_proficiency=None)`
- `Co-location = "Yes"` → `co_location_required = True`
- `Notes / Constraints` → `OpenRole.description` (verbatim, no redaction)
- `Location` → AD-086 `Location` model (city extraction + remote/onsite parsing)

**Import boundary:** mirrors `ingest/parse/csv.py` pattern but does NOT import `dsm/ingest/`.

### 3.5 `dsm/match/gates.py` — REWRITE (`_location_passes` only)

`filter_candidates` keeps its structure. Rewrite only `_location_passes`:

```python
def _location_passes(candidate: Candidate, scorecard: TargetProfileScorecard) -> bool:
    if not scorecard.co_location_required:
        return candidate.location.country == scorecard.location.country
    role_city = scorecard.location.city
    if role_city is None:
        return False
    cand_city = candidate.location.city or ""
    onsite = {c.casefold() for c in candidate.location.onsite_cities}
    return cand_city.casefold() == role_city.casefold() or role_city.casefold() in onsite
```

Stays pure — imports only `dsm.models` + stdlib.

### 3.6 `dsm/index/retrieve.py` — NEW (exact filter only)

```python
def exact_hard_skill_filter(
    pool: EligiblePool,
    hard_skills: list[SkillRequirement],
) -> tuple[EligiblePool, list[Exclusion]]: ...
```

- `skill_set` set membership: `{s.name for s in hard_skills} ⊆ {s.name for s in candidate.skills}`
- Proficiency floor: for each hard skill with `min_proficiency`, candidate's matching skill
  `proficiency ≥ min_proficiency` (ordinal comparison on `ProficiencyLevel`)
- Excluded candidates → `Exclusion(reason=HARD_SKILL_MISMATCH, detail=...)`
- Deterministic, LLM-free

### 3.7 `dsm/match/rank.py` — KEEP (no change)

Verify existing `rank_assessments` matches the §6.10 sort. No code change.

---

## 4. AD-086 Location migration — full touch-point list

Splitting `remote_eligible` ripples beyond the model. **All** touch points:

### 4.1 `dsm/models.py`
- `Location`: replace `remote_eligible: bool = False` with `remote_within_country: bool = False` + `onsite_cities: frozenset[str] = frozenset()`

### 4.2 `dsm/ingest/silver.py::parse_location`
- Map `Chennai-open=Yes` → `onsite_cities=frozenset({"Chennai"})` (keep home `city`)
- Map `"Remote (India)"` → `remote_within_country=True, city=None`
- Plain city (e.g. `"Pune"`) → both defaults (`remote_within_country=False, onsite_cities=frozenset()`)

### 4.3 `dsm/index/models.py`
- `FilterFields`: replace `remote_eligible: bool` with `remote_within_country: bool` + `onsite_cities: list[str]`
- `CandidateIndexRecord`: replace `remote_eligible: bool` with `remote_within_country: bool` + `onsite_cities: list[str]` (store as `list[str]`, not `frozenset` — Milvus has no set type; rebuild `frozenset` only when hydrating a `Location`)
- `project_filter_fields`: map `loc.remote_within_country` + `sorted(loc.onsite_cities)`
- `build_record`: use the new fields

### 4.4 `dsm/index/milvus_store.py`
- Collection schema (~line 72): replace `add_field("remote_eligible", DataType.BOOL)` with:
  - `add_field("remote_within_country", DataType.BOOL)`
  - `add_field("onsite_cities", DataType.ARRAY, element_type=DataType.VARCHAR, max_capacity=16, max_length=128)`
- Insert row dict (~line 132): replace `"remote_eligible": record.remote_eligible` with new fields
- `ensure_collection` rebuilds the test `.db`, so no prod data migration in scope

### 4.5 `dsm/cli/commands.py::build_near_misses`
- Location-miss `gap_summary` (~line 48-88): reframe "not open to relocation" → "city not in role's onsite set"

### 4.6 `dsm/match/gates.py::_location_passes`
- Rewrite to AD-086 semantics (onsite vs distributed gate — see §3.5 above)

### 4.7 Tests and fixtures affected

| File | Change |
|------|--------|
| `tests/test_models.py` | `assert location.remote_eligible is False` → update for new fields |
| `tests/fixtures/__init__.py` | `_candidate(remote_eligible=...)` → split into `remote_within_country` + `onsite_cities`; update role_02 to exercise `onsite_cities` |
| `tests/match/test_gates.py` | Update `_candidate` helper, update/add gate tests for AD-086 semantics |
| `tests/ingest/test_silver_helpers.py` | `parse_location` assertions: `remote_eligible` → new fields |
| `tests/ingest/test_silver_normalize.py` | `remote_eligible` assertion → new fields |
| `tests/ingest/test_silver_e2e.py` | `remote_eligible` assertions → new fields |
| `tests/ingest/test_models.py` | `Location(city="Pune", remote_eligible=True)` → new fields |
| `tests/index/test_index_models.py` | `_DEFAULT_LOCATION`, `project_filter_fields` assertions → new fields |
| `tests/index/test_milvus_store.py` | `remote_eligible` in fixture → new fields |

---

## 5. Edge cases

| Edge case | Expected behaviour |
|-----------|--------------------|
| Empty CSV (no data rows) | `DemandParseOutcome(roles=[], skipped=[])` — not a block unless banner is also missing |
| Banner with unparseable date | Block the run (missing `demand_as_of`) |
| Row with zero skills | Log+skip (empty `Required Skills`) |
| `start_date` before `valid_as_of` and within staleness | `warn` (backfilling an overdue role) |
| Candidate with `city=None` against onsite role | Excluded (can't match `role.city`) |
| Role with `city=None` and onsite required | All candidates excluded (no city to match) |
| Candidate with empty skills against hard-skill filter | Excluded (`HARD_SKILL_MISMATCH`) |
| All candidates excluded by gates | Empty `EligiblePool`, exclusion log populated |
| All candidates excluded by hard-skill filter | Empty pool, `HARD_SKILL_MISMATCH` exclusions added |
| Proficiency exactly at floor | Passes (≥ is inclusive) |

---

## 6. Eval cases to add (for future eval harness, c-002)

| Invariant | Test |
|-----------|------|
| **Gates respected** | A candidate gated on location never appears in shortlist |
| **Hard-skill not cleared by adjacency** | A candidate missing a HARD skill is excluded even if an adjacent skill exists |
| **Freshness refuse blocks** | `staleness > max` → no shortlist produced |
| **ROLE-02 onsite fix** | Pune remote-only candidate excluded from Chennai onsite; Pune+`onsite_cities={"Chennai"}` candidate passes |
| **Near-miss ordering** | Availability misses rank above hard-skill-gap misses |

---

## 7. Dependencies

No new dependencies beyond `docs/tech.md`. All modules use:
- Python stdlib (`csv`, `datetime`, `pathlib`, `re`, `logging`)
- Pydantic v2 (already pinned)
- structlog (already pinned)
