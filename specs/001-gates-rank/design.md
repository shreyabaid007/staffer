# Design — 001 Gates & Rank

> Technical design for real gates + rank implementation.
> References: requirements.md in this folder, AD-002, AD-020–022, AD-041, AD-043, AD-060, AD-063.

## Modules touched

| Module | Change | Phase contract |
| --- | --- | --- |
| `dsm/match/gates.py` | Replace stub with real location + availability gates | `(list[Candidate], TargetProfileScorecard) → (EligiblePool, ExclusionLog)` |
| `dsm/match/rank.py` | Replace stub with real sort + tie-break + top-k | `(list[CandidateAssessment], ...) → ShortlistResult` |
| `dsm/cli/commands.py` | Add no-match path: detect empty pool → build `NoMatchResult` → render | Orchestrator |
| `tests/fixtures/__init__.py` | New: importable ROLE-01/02/03 seed fixtures | Test data |
| `tests/match/test_gates.py` | New: exhaustive gate unit tests | — |
| `tests/match/test_rank.py` | New: rank unit tests + determinism | — |
| `tests/cli/test_no_match.py` | New: orchestrator no-match path tests | — |

## Data contracts (all frozen — AD-060, do not modify)

### Inputs to gates

```python
# from dsm/models.py — used as-is
Candidate.location: Location  # .city: str, .remote_eligible: bool
Candidate.availability: AvailabilityState  # FreeNow | RollingOff | NewJoiner
TargetProfileScorecard.location: Location
TargetProfileScorecard.co_location_required: bool
TargetProfileScorecard.start_date: date
TargetProfileScorecard.availability_window_days: int  # default 14
```

### Outputs from gates

```python
EligiblePool(candidates: list[Candidate], scorecard_id: str)
ExclusionLog(exclusions: list[Exclusion])
Exclusion(candidate_email: str, reason: ExclusionReason, detail: str)
```

### Inputs to rank

```python
list[CandidateAssessment]  # .combined_score, .hard_skill_coverage, .desired_skill_coverage, .candidate.email
```

### Outputs from rank

```python
ShortlistResult(role_id, ranked_assessments, total_eligible, exclusion_log, config_snapshot)
```

### Orchestrator no-match output

```python
NoMatchResult(role_id: str, reason: str, near_misses: list[NearMiss], exclusion_log: ExclusionLog)
NearMiss(candidate_email: str, name: str, reason: str, gap_summary: str)
```

## gates.py — design

### Gate ordering

Location is checked first; availability second. If a candidate fails location, they are excluded immediately — availability is not checked (G-OUT-2). This avoids redundant exclusion records and keeps the log clean.

### Location gate logic

```
if scorecard.co_location_required:
    pass if candidate.location.city == scorecard.location.city
    pass if candidate.location.remote_eligible is True
    else → exclude LOCATION_MISMATCH
else:
    all pass (any India location)
```

City comparison: case-insensitive, stripped. Both values come from typed models so should be consistent, but defensive normalisation costs nothing.

### Availability gate logic

```
deadline = scorecard.start_date + timedelta(days=scorecard.availability_window_days)

match candidate.availability:
    FreeNow        → always pass
    RollingOff     → pass if expected_date <= deadline
    NewJoiner      → pass if join_date <= deadline
```

`availability_window_days` is read from the scorecard (which gets it from config via clarify). Never hardcoded to 14.

### Detail string format

Human-readable, not machine-parsed:
- Location: `"Candidate is in {city}; role requires {role_city} (co-location)"`
- Availability: `"Available {date}; role deadline is {deadline} (start {start} + {window}d)"`

### Import constraints

`gates.py` imports ONLY from `dsm.models` and Python stdlib (`datetime`). No imports from `dsm.pii`, `dsm.index`, `dspy`, `modal`, `httpx`. Enforced by import-linter contract in `pyproject.toml`.

## rank.py — design

### Sort key

```python
sorted(assessments, key=lambda a: (
    -a.combined_score,
    -a.hard_skill_coverage,
    -a.desired_skill_coverage,
    a.candidate.email,  # ascending for determinism
))[:top_k]
```

### Config snapshot

`config_snapshot` captures: `top_k`, `weights` (skill/feedback), model IDs — for reproducibility tracing. Read from `config/default.yaml` via the existing config loader.

### Empty assessments

When assessments is empty, return `ShortlistResult(ranked_assessments=[], total_eligible=0, ...)`. The orchestrator handles the no-match path — rank does not.

## Orchestrator no-match path — design

In `dsm/cli/commands.py`, after `filter_candidates`:

```python
if not eligible_pool.candidates:
    near_misses = build_near_misses(candidates, scorecard, exclusion_log)
    result = NoMatchResult(
        role_id=role.role_id,
        reason="No candidates passed eligibility gates.",
        near_misses=near_misses[:3],  # AD-063(d)
        exclusion_log=exclusion_log,
    )
    typer.echo(result.model_dump_json(indent=2))
    return
```

### `build_near_misses` logic

For each exclusion in `exclusion_log.exclusions`, look up the original `Candidate` by email, then:

1. **Availability miss**: compute `overshoot_days = free_date - deadline`. Sort key: `(0, overshoot_days)`.
2. **Location miss**: sort key: `(1, 0 if remote_eligible else 1)`.

The `(type_rank, ...)` tuple gives availability-first ordering per AD-063(b). Build `NearMiss` with:
- `candidate_email`, `name` from the candidate
- `reason`: the `ExclusionReason` value
- `gap_summary`: e.g. `"available 2 days after deadline"` or `"in Pune, not open to relocation"`

Cap at 3 per AD-063(d).

## Test fixtures — design

All fixtures live in `tests/fixtures/__init__.py` as importable Python functions/constants. Each returns `(list[Candidate], OpenRole)` or `(list[Candidate], TargetProfileScorecard)`.

### ROLE-01 — partial availability exclusion

- **Role**: Kotlin dev, Chennai, co-location=True, start=2026-07-01, window=14d → deadline 2026-07-15
- **Aarav**: RollingOff, expected_date=2026-08-01, Chennai → excluded on availability (17 days over)
- **Karan**: Beach (FreeNow), Chennai → passes both gates
- **Vivaan**: RollingOff, expected_date=2026-07-10, Chennai → passes (5 days before deadline)
- **Rahul**: Beach (FreeNow), Chennai → passes
- **Vikram**: NewJoiner, join_date=2026-07-14, Chennai → passes (exactly +13d, within window). Source=NEW_JOINER, skills flagged `unverified`.

### ROLE-02 — Chennai co-location filter

- **Role**: React dev, Chennai, co-location=True, start=2026-07-01, window=14d
- Reuse ROLE-01 candidates plus:
- **Deepa**: FreeNow, Pune, remote_eligible=False → excluded on location
- **Nikhil**: FreeNow, Bangalore, remote_eligible=False → excluded on location
- **Priya**: FreeNow, Pune, remote_eligible=True → passes (remote_eligible)
- Chennai-based candidates (Karan, Rahul, etc.) pass.

### ROLE-03 — total exclusion (empty pool)

- **Role**: Java dev, Mumbai, co-location=True, start=2026-07-01, window=14d → deadline 2026-07-15
- **Candidate A**: RollingOff, expected_date=2026-07-16, Mumbai → availability miss, overshoot 1 day
- **Candidate B**: RollingOff, expected_date=2026-08-15, Mumbai → availability miss, overshoot 31 days
- **Candidate C**: FreeNow, Pune, remote_eligible=True → location passes (remote_eligible), but wait — this passes. Need all to fail.

Correction — all candidates must fail for ROLE-03:
- **Candidate A**: RollingOff, expected_date=2026-07-16, Pune, remote_eligible=False → fails location (Pune ≠ Mumbai, not remote_eligible). Near-miss: location, not remote_eligible.
- **Candidate B**: FreeNow, Delhi, remote_eligible=True → passes location (remote_eligible) but... FreeNow always passes availability. This candidate would pass both gates.

Design constraint: to fail ALL candidates, every candidate must fail at least one gate. With co-location=True + Mumbai:
- **Sanjay**: RollingOff, expected_date=2026-07-16, Mumbai, remote_eligible=False → passes location (city match), fails availability by 1 day. Near-miss: availability, overshoot=1.
- **Meera**: NewJoiner, join_date=2026-08-15, Mumbai, remote_eligible=False → passes location, fails availability by 31 days. Near-miss: availability, overshoot=31.
- **Arjun**: FreeNow, Pune, remote_eligible=False → passes availability (FreeNow), fails location (Pune ≠ Mumbai, not remote_eligible). Near-miss: location, not remote_eligible.
- **Kavita**: FreeNow, Delhi, remote_eligible=True → passes availability (FreeNow), passes location (remote_eligible). **This would pass.** Must not include candidates who pass both gates.

Final ROLE-03 fixture (all fail):
- **Sanjay**: RollingOff, expected_date=2026-07-16, Mumbai → passes location, fails availability by 1 day.
- **Meera**: NewJoiner, join_date=2026-08-15, Mumbai → passes location, fails availability by 31 days.
- **Arjun**: FreeNow, Pune, remote_eligible=False → passes availability, fails location.
- **Kavita**: FreeNow, Kolkata, remote_eligible=False → passes availability, fails location.

Near-miss ordering per AD-063(b): Sanjay (avail, +1d) → Meera (avail, +31d) → Arjun (loc, not remote) / Kavita (loc, not remote) → tie-break alphabetically. Capped at 3: Sanjay, Meera, Arjun.

## Eval cases to add

These fixtures become the foundation for C3's live eval suite. Keep them importable so `dsm/eval/` can reuse them without duplication.

| Case | Invariant tested |
| --- | --- |
| ROLE-01 | gates-respected (Aarav excluded on availability) |
| ROLE-02 | gates-respected (location filtering) |
| ROLE-03 | no-match path (AD-041), near-miss ordering (AD-063b), near-miss cap (AD-063d) |

## Edge cases

- **Boundary date**: candidate free exactly on deadline day → passes (≤, not <).
- **Boundary date +1**: candidate free one day after deadline → excluded.
- **Both gates fail**: location checked first; only location exclusion recorded (G-OUT-2).
- **Empty candidate list**: gates returns empty EligiblePool + empty ExclusionLog; orchestrator produces NoMatchResult with empty near_misses.
- **All candidates pass**: normal flow — no exclusions, rank sorts and returns top-k.
- **Fewer than top-k eligible**: rank returns all of them (no padding).
- **City case sensitivity**: `"chennai"` == `"Chennai"` — normalise to lowercase.
