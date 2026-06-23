# Design — b-004 Near-Miss Skill Verdict + Selection Rationale

> Implements `requirements.md`. **Part 1** (skill verdict) changes one function —
> `dsm/cli/commands.py::build_near_misses` — deterministically, no model change. **Part 2**
> (selection rationale) adds an optional `NearMiss` field + an injected LLM seam threaded through
> the no-match path. No gate change in either part.

## Modules touched

| File | Change | Part |
|------|--------|------|
| `dsm/cli/commands.py` | `build_near_misses` — render hard-skill verdict + availability sub-ordering | 1 |
| `dsm/models.py` | Add `NearMiss.selection_rationale: str \| None = None` (additive, optional) | 2 |
| `dsm/match/score.py` *(or new `dsm/match/near_miss.py`)* | `NearMissRationalePredictor` type alias + `make_near_miss_rationale_predictor(lm)` (DSPy `Signature` over `PseudonymisedLM`) + `explain_near_miss(...)` applier (skip-on-error) | 2 |
| `dsm/cli/commands.py` | `run_match` / `_no_match` — accept + thread the rationale predictor; generate rationale for the capped ≤3 near-misses; `_match_role` builds the live predictor at the CLI edge | 2 |
| `config/prompts/*` + `config/default.yaml` | New `near_miss_rationale` prompt (loaded via `load_prompt`) | 2 |
| `docs/decision.md` | Append **AD-095**, **AD-096** (T-000) | — |
| `tests/cli/test_no_match.py` | New cases (FR-1, FR-2, FR-5, FR-6, FR-9, FR-10) | 1+2 |

No change to: `dsm/match/gates.py`, `dsm/index/retrieve.py`, the exact filter, scoring math,
ranking, `explain` lineage shape (it already serialises whatever `NearMiss` carries).

## Data contracts

Part 1 keeps the verdict inside `gap_summary` (no new field). Part 2 adds **one** optional field
(AD-096 / AD-060 amendment):

```python
class NearMiss(BaseModel, frozen=True):
    candidate_email: str
    name: str
    reason: str
    gap_summary: str                          # Part 1: skill verdict appended here
    selection_rationale: str | None = None    # Part 2: LLM "why consider once gap resolved"; None on cap/error
```

`frozen=True` is unchanged; the field is additive, optional, and defaulted, so every existing
`NearMiss(...)` call site (gates tests, fixtures) keeps validating. The injected seam:

```python
# in dsm/match/score.py (or dsm/match/near_miss.py) — mirrors ScorePredictor
NearMissRationalePredictor = Callable[[TargetProfileScorecard, Candidate, str], str]
#   (scorecard, candidate, gap_summary) -> rationale text
```

## Phase

Query-time, no-match path only (`run_match` → `_no_match` → `build_near_misses`). Part 1 is pure
Python (no I/O, no LLM). Part 2 makes ≤3 LLM calls through `PseudonymisedLM` after the cap, only
when a no-match occurs.

## Algorithm — Part 1: skill verdict (revised `build_near_misses`)

`build_near_misses(candidates, scorecard, exclusion_log)` already holds everything it needs:
the candidate objects (`by_email`) and `scorecard.hard_depth_skills`.

1. **Compute the skill verdict once, for the pre-skill-filter near-miss candidates.** Collect the
   `Candidate` objects behind every `AVAILABILITY_MISMATCH` / `LOCATION_MISMATCH` exclusion and run
   them through the existing public filter in a single call:

   ```python
   from dsm.models import EligiblePool
   # reuse — never re-implement — the real hard-skill rule (FR-3, AD-072/033)
   pre_filter = [by_email[e.candidate_email]
                 for e in exclusion_log.exclusions
                 if e.reason in (ExclusionReason.AVAILABILITY_MISMATCH,
                                 ExclusionReason.LOCATION_MISMATCH)
                 and e.candidate_email in by_email]
   cleared_pool, _ = exact_hard_skill_filter(
       EligiblePool(candidates=pre_filter, scorecard_id=scorecard.role_id),
       scorecard.hard_depth_skills,
   )
   clears_skills = {c.email for c in cleared_pool.candidates}   # authoritative verdict
   ```

   `clears_skills` membership is the single source of truth for "would clear hard skills" — it
   honours the proficiency floor and no-adjacency rule exactly (FR-3-AC-1). The dropped-exclusions
   half of the tuple is discarded (we only need the boolean per candidate). When
   `hard_depth_skills` is empty, `exact_hard_skill_filter` passes everyone, so every candidate is
   in `clears_skills` (FR-5).

2. **Render the verdict into `gap_summary`.** Per exclusion:
   - `AVAILABILITY_MISMATCH`: keep the existing `"available {overshoot} day(s) after deadline"`
     prefix, then append:
     - cleared → `"; clears all hard skills"`
     - gap → `"; also missing {n} hard skill(s): {names}"`
   - `LOCATION_MISMATCH`: keep `"in {city}, not in onsite set for {role_city}"`, then append the
     same `"; clears all hard skills"` / `"; also missing …"` suffix.
   - `HARD_SKILL_MISMATCH`: **unchanged** (FR-7).

   The missing-skill **names** are recomputed structurally (membership against
   `scorecard.hard_depth_skills`), exactly as the existing `HARD_SKILL_MISMATCH` branch already
   does (`held = {s.name for s in candidate.skills}` → sorted missing). `Exclusion.detail` is never
   read (FR-4). Edge note: a candidate present-but-below-floor is in the "gap" bucket via
   `clears_skills` yet contributes no *missing-by-membership* name; render `"; also missing hard
   skills (below required proficiency)"` (n via the floor check) — or, simplest and still honest,
   fall back to the count from `clears_skills` and the membership names, labelling proficiency
   shortfalls generically. (See "Below-floor edge".)

3. **Ordering** — widen every sort key to a uniform 4-tuple
   `(type_rank, sub_rank, metric, candidate_email)`:

   | Near-miss | sort key | rationale |
   |-----------|----------|-----------|
   | availability, clears skills | `(0, 0, overshoot, email)` | actionable — shift date → eligible |
   | availability, skill gap     | `(0, 1, overshoot, email)` | date shift alone insufficient (FR-6-AC-1) |
   | location                    | `(1, 0, 0, email)`        | structural (AD-063b); skill text informational |
   | hard-skill                  | `(2, 0, missing_count, email)` | AD-088, unchanged |

   Cross-type order (availability < location < hard-skill) and the top-3 cap are unchanged
   (FR-6-AC-2). Sorting a 4-tuple is still a plain `ranked.sort(key=…)`.

## Algorithm — Part 2: selection rationale (LLM, top-3 only)

`build_near_misses` stays deterministic and rationale-free — it returns the fully ordered list as
today. The rationale is applied **after** the AD-063d cap, so we only ever pay for the ≤3 shown:

1. **Seam + applier** (in `dsm/match/score.py` or a new `dsm/match/near_miss.py`), mirroring
   `make_score_predictor`:

   ```python
   class NearMissRationale(dspy.Signature):
       """Explain why a near-miss is worth considering once its gap is resolved (config/prompts)."""
       role: TargetProfileScorecard = dspy.InputField()
       candidate_skills: list[str] = dspy.InputField()
       candidate_feedback: list[str] = dspy.InputField()
       gap: str = dspy.InputField()                # the near-miss gap_summary
       rationale: str = dspy.OutputField()

   def make_near_miss_rationale_predictor(lm) -> NearMissRationalePredictor:
       sig = NearMissRationale.with_instructions(load_prompt("near_miss_rationale"))
       predictor = dspy.Predict(sig)
       def _predict(scorecard, candidate, gap):
           with dspy.context(lm=lm):
               return predictor(role=scorecard,
                                candidate_skills=[f"{s.name} {s.proficiency.value}" for s in candidate.skills],
                                candidate_feedback=[e.text for e in candidate.feedback.entries],
                                gap=gap).rationale
       return _predict
   ```

   It is fed only structured, PII-free inputs — never `candidate.name`/`email` (FR-10-AC-1).

2. **Apply after the cap.** `_no_match` builds the ordered list (`build_near_misses`), slices
   `[:3]`, then for each survivor calls the predictor and reconstructs the frozen `NearMiss` with
   `selection_rationale` set (`nm.model_copy(update={"selection_rationale": text})`). On any
   predictor exception, log + leave `selection_rationale=None` and keep the near-miss (FR-9-AC-4),
   exactly like `score_candidate`'s skip-on-error. The predictor is **optional** — when `None`
   (the pure-unit path / tests that don't exercise rationale), every rationale stays `None`.

3. **Wiring.** `run_match` gains a `near_miss_predict: NearMissRationalePredictor | None = None`
   param (default `None` keeps existing call sites valid) and passes it into both `_no_match`
   call-sites. `_match_role` builds the live predictor over `PseudonymisedLM` at the CLI edge
   (next to `_build_score_predictor`), monkeypatched in CLI tests. This is the only `run_match`
   signature change; the gate→filter→score→rank spine is untouched (FR-8 / NF-4).

## Edge cases

- **FreeNow** candidates never produce an `AVAILABILITY_MISMATCH` (the gate always passes them),
  so they never reach the availability branch — no special-casing needed.
- **Empty `hard_depth_skills`** → everyone clears (FR-5); both availability and location near
  misses read `"… ; clears all hard skills"`. (Arguably noise when there is no hard requirement;
  acceptable and honest. If undesirable, omit the suffix when `hard_depth_skills` is empty — call
  it out at review.)
- **Below-floor edge** — `exact_hard_skill_filter` drops a candidate who holds the skill but below
  `min_proficiency`. `clears_skills` reflects this correctly (they're in the gap bucket), but the
  membership-only name recompute won't list that skill. Keep the verdict honest: count from the
  authoritative filter; if names can't be fully enumerated by membership, use the generic
  "below required proficiency" phrasing. Covered by an eval case.
- **Location near-miss with no role city** — existing wording uses `"required city"` fallback;
  the appended skill suffix is independent of city and unaffected.
- **Exclusion without a matching candidate** — already skipped defensively (`by_email.get` →
  `continue`); the pre-filter collection uses the same guard.

## Reuse / no-duplication

`build_near_misses` already imports `exact_hard_skill_filter` and `EligiblePool` is already a
public model — no new import surface beyond `EligiblePool` (and it may already be imported).
The verdict is computed by the **same** function the real pipeline uses, so the no-match
explanation cannot drift from the gate (Golden rule: one source of truth). No private symbol in
`retrieve.py` is touched, so `dsm/index` is unchanged (FR-8).

## Eval / test cases to add (`tests/cli/test_no_match.py`)

1. **FR-1-AC-2** — availability miss, candidate holds all hard skills → `gap_summary` contains
   `"clears all hard skills"`.
2. **FR-1-AC-3** — availability miss, candidate missing a hard skill → `gap_summary` contains
   `"also missing"` + the skill name; **not** `"clears all hard skills"`.
3. **FR-2-AC-1** — location miss carries the same verdict suffix.
4. **FR-5** — empty `hard_depth_skills` → availability miss reads `"clears all hard skills"`.
5. **FR-6-AC-1** — two availability misses, equal overshoot, one clears / one has a gap → the
   skill-clearing one is ordered first.
6. **FR-6-AC-2 / FR-7** — cross-type order (availability < location < hard-skill) and the existing
   `test_no_match` ordering assertions still pass; top-3 cap unchanged.
7. **Below-floor** — candidate holds a hard skill below `min_proficiency` → counted as a gap, not
   "clears all hard skills".
8. **Determinism** — same input → identical near-miss **set + ordering + `gap_summary`** (re-run
   equality on the deterministic fields), mirroring the existing invariant. `selection_rationale`
   is excluded from the equality (LLM prose, like the shortlist narrative).
9. **FR-9-AC-2** — with a fake predictor that records its calls, a no-match with >3 near-misses
   invokes the predictor exactly 3 times (only the shown ones get a rationale).
10. **FR-9-AC-4** — a predictor that raises → those near-misses returned with
    `selection_rationale=None`; the no-match still succeeds.
11. **FR-10** — the fake predictor asserts its inputs contain no `name`/`email` (only skills,
    feedback, gap); confirms the rationale path is PII-free by construction.

## ADRs (appended to `docs/decision.md` at T-000)

**AD-095** (skill verdict; no frozen-contract change) and **AD-096** (`NearMiss.selection_rationale`
field + LLM seam; AD-060 amendment, requires sign-off) as stated in `requirements.md`. Both cite
`specs/b-004-near-miss-skill-verdict/`. AD-095 refines AD-063b ordering + AD-088 actionability;
AD-096 parallels AD-092 (a prior additive frozen-enum amendment ratified mid-slice).
