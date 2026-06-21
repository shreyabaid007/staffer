# B-001 Query-Time Deterministic Foundation — Requirements

> **Lane:** B (query-time retrieval) · **Slice:** B-1  
> **Architecture ref:** `ee-query-architecture.md` §5, §6.0–6.5, §6.10, §13  
> **Prerequisite:** a-005 index upsert (merged), c-001 gates+rank (deprecated, AD-085)  

---

## User story

As the staffing decision engine, given a demand-side Open Roles CSV and a supply-side
candidate pool, I can **deterministically parse** the demand, **guard freshness**,
**gate** candidates on location + availability, **filter** on hard-skill exact match,
and **rank** the survivors — so the LLM-dependent scoring slice (B-2) receives a clean,
typed, deterministic foundation with every exclusion explained.

---

## Functional requirements (EARS format)

### FR-1 · Parse demand CSV → typed `OpenRole`s

**When** the system receives an Open Roles CSV path,  
**the system shall** parse the banner line into `demand_as_of: date`, split each row's
`Required Skills` on `;` into `SkillRequirement`s (HARD when a proficiency qualifier is
present, DESIRED when bare or `(nice to have)`), map `Co-location` → `co_location_required`,
map `Notes / Constraints` → `OpenRole.description`, map `Location` to the AD-086 location
model, and order the batch by `Priority`.

**Where** a row is malformed (unparseable `Start`, missing `Role ID`, empty `Required Skills`),  
**the system shall** log the reason + payload, skip the row, and count the skip in
`DemandParseOutcome.skipped`.

**Where** the banner is missing or unparseable,  
**the system shall** block the run (non-zero exit) because the freshness guard cannot
operate without `demand_as_of`.

| AC | Criterion |
|----|-----------|
| FR-1-AC-1 | Banner `"Open Roles - … - as of 15 Jun 2026"` → `demand_as_of = date(2026, 6, 15)` |
| FR-1-AC-2 | `"kotlin (expert)"` → `SkillRequirement(name="kotlin", depth=HARD, min_proficiency=EXPERT)` |
| FR-1-AC-3 | `"aws"` (bare) → `SkillRequirement(name="aws", depth=DESIRED, min_proficiency=None)` |
| FR-1-AC-4 | `"kafka (nice to have)"` → `SkillRequirement(name="kafka", depth=DESIRED, min_proficiency=None)` |
| FR-1-AC-5 | `Notes / Constraints` value → `OpenRole.description` verbatim; no redaction |
| FR-1-AC-6 | Malformed row → logged + skipped + counted; valid rows still parsed |
| FR-1-AC-7 | Missing banner → blocks the run; `DemandParseOutcome` not returned |
| FR-1-AC-8 | Batch ordered by `Priority` (ascending) |

### FR-2 · Freshness guard

**When** the system has `demand_as_of` and supply `valid_as_of`,  
**the system shall** compute `staleness_days = (demand_as_of − valid_as_of).days` and
decide per the pinned decision tree:

| Condition | Action |
|-----------|--------|
| `staleness_days ≤ max_staleness_days` (including supply fresher than demand) | **ok** |
| `0 < staleness_days ≤ max_staleness_days` **and** `start_date < valid_as_of` | **warn** |
| `staleness_days > max_staleness_days` | **refuse** |

| AC | Criterion |
|----|-----------|
| FR-2-AC-1 | Supply fresher than demand (`staleness ≤ 0`) → `ok` |
| FR-2-AC-2 | `staleness > max_staleness_days` → `refuse` |
| FR-2-AC-3 | `0 < staleness ≤ max` AND `start_date < valid_as_of` → `warn` |
| FR-2-AC-4 | `0 < staleness ≤ max` AND `start_date ≥ valid_as_of` → `ok` |
| FR-2-AC-5 | `refuse` blocks the run (non-zero exit, no shortlist) |

### FR-3 · Gate pre-filter (AD-086 location model)

**When** the system gates candidates,  
**the system shall** apply the AD-086 location semantics:

- **Onsite gate** (`co_location_required = True`): candidate passes **iff**
  `role.city is not None and (candidate.city == role.city OR role.city in candidate.onsite_cities)`
  (case-insensitive). `remote_within_country` does NOT clear an onsite gate.
- **Distributed gate** (`co_location_required = False`): candidate passes **iff**
  `candidate.country == role.country`.

**the system shall** apply the existing availability gate unchanged (AD-021/022).

| AC | Criterion |
|----|-----------|
| FR-3-AC-1 | Onsite: `role.city=None` → candidate excluded (no city to match) |
| FR-3-AC-2 | Onsite: `onsite_cities={"Chennai"}` + role Chennai + candidate Pune → **pass** |
| FR-3-AC-3 | Onsite: `remote_within_country=True` + candidate Pune + role Chennai → **fail** |
| FR-3-AC-4 | Onsite: case-insensitive city matching (`"chennai"` == `"Chennai"`) |
| FR-3-AC-5 | Distributed: same-country → pass; different-country → fail |
| FR-3-AC-6 | Location failure short-circuits availability (G-OUT-2 unchanged) |
| FR-3-AC-7 | Empty pool → exclusions flow cleanly (no exception) |

### FR-4 · Exact hard-skill filter

**When** the system applies the exact hard-skill filter after gates,  
**the system shall** require `{s.name for s in hard_skills} ⊆ candidate.skill_set` (exact
set membership) + `Skill.proficiency ≥ min_proficiency` floor for each hard skill carrying
a proficiency qualifier.

**The system shall not** consult adjacency (AD-033/072) — adjacency contributes only to
desired-skill coverage downstream.

**Where** a candidate fails the hard-skill filter,  
**the system shall** log the exclusion as `ExclusionReason.HARD_SKILL_MISMATCH` (AD-088).

| AC | Criterion |
|----|-----------|
| FR-4-AC-1 | `hard_skills ⊆ skill_set` passes; missing skill → excluded |
| FR-4-AC-2 | Proficiency floor: `INTERMEDIATE` required, candidate has `BEGINNER` → excluded |
| FR-4-AC-3 | Adjacency never consulted for hard skills |
| FR-4-AC-4 | Exclusion logged as `HARD_SKILL_MISMATCH` with detail |
| FR-4-AC-5 | Empty pool → exclusions only (no exception) |

### FR-5 · Rank (KEEP — verify only)

**The system shall** rank assessments by `combined_score` desc → `hard_skill_coverage` desc →
`desired_skill_coverage` desc → `candidate.email` asc; truncate to `top_k`.

| AC | Criterion |
|----|-----------|
| FR-5-AC-1 | Existing `rank_assessments` matches the sort order above |
| FR-5-AC-2 | No behavioural change; existing tests pass |

---

## Non-functional requirements

| ID | Requirement |
|----|-------------|
| NF-1 | `make check` GREEN after every commit (format, lint, typecheck, all tests, import contracts) |
| NF-2 | `gates.py` imports only `dsm.models` + stdlib — no `pii/`, `index/`, `ingest/`, or LLM code |
| NF-3 | `dsm/match/demand.py` does not import `dsm/ingest/` (mirrors ingest CSV parser but is independent) |
| NF-4 | No new dependencies beyond `docs/tech.md` |
| NF-5 | All existing tests pass — no regressions from the `Location` / `ExclusionReason` amendments |
| NF-6 | One task = one commit, imperative, referencing the spec |
| NF-7 | Deterministic: same input → same output (no randomness, no LLM) |

---

## Frozen-contract amendments (require sign-off — `T-000-ADR`)

| ADR | Amendment | Touches |
|-----|-----------|---------|
| **AD-086** | Split `Location.remote_eligible` → `remote_within_country: bool` + `onsite_cities: frozenset[str]` | `dsm/models.py::Location` (frozen, AD-060), ingestion silver, index models, Milvus schema, gates, fixtures, tests |
| **AD-087** | Query-time as-of freshness guard (ok/warn/refuse) | New module only; reuses `config.reconcile.max_staleness_days` |
| **AD-088** | Add `ExclusionReason.HARD_SKILL_MISMATCH` | `dsm/models.py::ExclusionReason` (frozen, AD-060) |

---

## Out of scope (B-2)

- Clarify (LLM-dependent)
- Hybrid recall (dense + BM25 + RRF)
- Rerank (cross-encoder)
- Score + combine (LLM sub-scores)
- Full orchestrator wiring
- `dsm explain` CLI
- Open Roles CSV fixture in `data/raw/demand/`
