# Requirements — 001 Gates & Rank

> Spec for replacing the gates and rank stubs with real implementations.
> References: AD-002, AD-020, AD-021, AD-022, AD-041, AD-043, AD-060, AD-063.

## User story

As a staffing manager, when I run `dsm match --role-id <id>`, I want candidates who cannot meet the role's location or availability requirements to be **excluded before scoring**, with a clear reason for each exclusion, so I only review genuinely eligible people — and when nobody qualifies, I see the closest near-misses rather than silence.

## Acceptance criteria (EARS format)

### Gates — location

**G-LOC-1** WHEN `co_location_required` is `True` AND `candidate.location.city == scorecard.location.city`, the system SHALL include the candidate in the `EligiblePool`.

**G-LOC-2** WHEN `co_location_required` is `True` AND `candidate.location.city != scorecard.location.city` AND `candidate.location.remote_eligible` is `True`, the system SHALL include the candidate in the `EligiblePool` (AD-063a).

**G-LOC-3** WHEN `co_location_required` is `True` AND `candidate.location.city != scorecard.location.city` AND `candidate.location.remote_eligible` is `False`, the system SHALL exclude the candidate with `reason=ExclusionReason.LOCATION_MISMATCH` and a `detail` string containing both the candidate's city and the role's city.

**G-LOC-4** WHEN `co_location_required` is `False`, the system SHALL include all candidates regardless of city (any India location passes).

### Gates — availability

**G-AVL-1** WHEN candidate availability is `FreeNow`, the system SHALL include the candidate in the `EligiblePool` regardless of `scorecard.start_date`.

**G-AVL-2** WHEN candidate availability is `RollingOff` AND `expected_date <= scorecard.start_date + timedelta(days=scorecard.availability_window_days)`, the system SHALL include the candidate in the `EligiblePool`.

**G-AVL-3** WHEN candidate availability is `RollingOff` AND `expected_date > scorecard.start_date + timedelta(days=scorecard.availability_window_days)`, the system SHALL exclude the candidate with `reason=ExclusionReason.AVAILABILITY_MISMATCH` and a `detail` string containing both the candidate's `expected_date` and the role's deadline (`start_date + availability_window_days`).

**G-AVL-4** WHEN candidate availability is `NewJoiner` AND `join_date <= scorecard.start_date + timedelta(days=scorecard.availability_window_days)`, the system SHALL include the candidate in the `EligiblePool`.

**G-AVL-5** WHEN candidate availability is `NewJoiner` AND `join_date > scorecard.start_date + timedelta(days=scorecard.availability_window_days)`, the system SHALL exclude the candidate with `reason=ExclusionReason.AVAILABILITY_MISMATCH` and a `detail` string containing both `join_date` and the deadline.

**G-AVL-6** WHEN candidate availability is `RollingOff` with `confidence="low"`, the gate SHALL still use `expected_date` (AD-022). Low confidence is surfaced downstream as a `ROLL_OFF_UNCERTAIN` flag, not gated here.

### Gates — output contract

**G-OUT-1** The system SHALL return `(EligiblePool, ExclusionLog)` using the frozen models from `dsm/models.py`. Gates SHALL NOT return `NoMatchResult`.

**G-OUT-2** WHEN a candidate fails both gates, the system SHALL record only the first failing gate (location checked before availability) to avoid redundant exclusions.

### Rank

**R-SORT-1** The system SHALL sort `CandidateAssessment` entries by `combined_score` descending.

**R-TIE-1** WHEN two assessments have equal `combined_score`, the system SHALL break ties by `hard_skill_coverage` descending, then `desired_skill_coverage` descending, then `candidate.email` ascending (lexicographic). This guarantees deterministic output.

**R-TOP-1** The system SHALL return at most `top_k` assessments (default 5 from `config/default.yaml` `ranking.top_k`, per AD-043).

**R-OUT-1** WHEN the assessments list is empty, the system SHALL return `ShortlistResult` with an empty `ranked_assessments` list. Rank SHALL NOT build `NoMatchResult`.

### Orchestrator — no-match path

**O-NM-1** WHEN `eligible_pool.candidates` is empty, the orchestrator SHALL build a `NoMatchResult` with a human-readable `reason` and `near_misses` populated from the original candidates + scorecard (AD-063c).

**O-NM-2** Near-misses SHALL be ordered per AD-063(b): availability misses first (smallest overshoot in days), then location misses (`remote_eligible=True` before `False`).

**O-NM-3** Near-misses SHALL be capped at 3 (AD-063d).

**O-NM-4** The orchestrator SHALL recompute gaps from structured `Candidate` + `TargetProfileScorecard` data — it SHALL NOT parse `Exclusion.detail`.

**O-NM-5** The CLI SHALL render the `NoMatchResult` (reason + near-misses with gap summaries) to the user.

### Seed eval fixtures (EARS)

**E-R01** WHEN the system processes ROLE-01, it SHALL exclude Aarav on `AVAILABILITY_MISMATCH` with a `detail` containing both the candidate's free-date and the role's deadline.

**E-R02** WHEN the system processes ROLE-02 (co-location Chennai), it SHALL exclude every candidate who is neither Chennai-based nor `remote_eligible`.

**E-R03** WHEN the system processes ROLE-03, it SHALL produce `EligiblePool(candidates=[])` and the orchestrator SHALL produce a `NoMatchResult` with `near_misses` ordered per AD-063(b): availability misses first (smallest overshoot first), then location misses (`remote_eligible=True` before `False`), capped at 3.

## Non-requirements

- Scoring logic (Lane B) — assessments are stubbed; rank only sorts what it receives.
- PII boundary — gates and rank never see PII concerns; they operate on typed models.
- Multi-gate pass (e.g. skill gate) — out of scope; only location and availability gates exist in MVP.
