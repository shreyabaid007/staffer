# Design — b-004 Near-Miss Skill Verdict + Selection Rationale

> Implements `requirements.md`. **Part 1** (skill verdict) changes one function —
> `dsm/cli/commands.py::build_near_misses` — deterministically, no model change. **Part 2**
> (selection rationale) adds an optional `NearMiss` field + an injected LLM seam threaded through
> the no-match path. No gate change in either part.

## Modules touched

| File | Change | Part |
|------|--------|------|
| `dsm/cli/commands.py` | `build_near_misses` — keep only availability/location misses that clear hard skills (AD-097); plain gap wording; AD-063b ordering | 1 |
| `dsm/models.py` | Add `NearMiss.selection_rationale: str \| None = None` (additive, optional) | 2 |
| `dsm/match/score.py` *(or new `dsm/match/near_miss.py`)* | `NearMissRationalePredictor` type alias + `make_near_miss_rationale_predictor(lm)` (DSPy `Signature` over `PseudonymisedLM`) + `explain_near_miss(...)` applier (skip-on-error) | 2 |
| `dsm/cli/commands.py` | `run_match` / `_no_match` — accept + thread the rationale predictor; generate rationale for the capped ≤3 near-misses; `_match_role` builds the live predictor at the CLI edge | 2 |
| `config/prompts/*` + `config/default.yaml` | New `near_miss_rationale` prompt (loaded via `load_prompt`) | 2 |
| `docs/decision.md` | Append **AD-095/AD-096** (T-000); **AD-097** supersedes AD-095/AD-088 | — |
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

## Algorithm — Part 1: clear-hard-skills filter (revised `build_near_misses`, AD-097)

`build_near_misses(candidates, scorecard, exclusion_log)` already holds everything it needs: the
candidate objects (`by_email`) and `scorecard.hard_depth_skills`.

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
   honours the proficiency floor and no-adjacency rule exactly (FR-3-AC-1). When `hard_depth_skills`
   is empty, the filter passes everyone, so every gate miss clears (FR-5).

2. **Keep only clearers; emit plain gap wording.** Per exclusion:
   - If `candidate.email not in clears_skills` → **skip** (not a near-miss; AD-097). This drops both
     the "double miss" (gate + skill gap) and the below-floor case.
   - `AVAILABILITY_MISMATCH` (clearer): `gap_summary = "available {overshoot} day(s) after
     deadline"`; `sort_key = (0, overshoot, email)`.
   - `LOCATION_MISMATCH` (clearer): `gap_summary = "in {city}, not in onsite set for {role_city}"`;
     `sort_key = (1, 0, email)`.
   - `HARD_SKILL_MISMATCH`: **skip** — never a near-miss (FR-7). (These candidates aren't in
     `clears_skills` either, since the filter only ran on gate misses, so the membership check
     already excludes them; the branch is explicit for clarity.)

   `Exclusion.detail` is never read (FR-4). No skill suffix — every surfaced near-miss clears skills
   by construction, so the verdict is implicit.

3. **Ordering (AD-063b, unchanged).** Three-tuple `(type_rank, metric, candidate_email)`:
   availability `(0, overshoot, email)` then location `(1, 0, email)`; plain `ranked.sort(...)`.
   The top-3 cap (AD-063d) is applied by the caller. No skill-based sub-rank (the AD-095 4-tuple is
   reverted) — there are no skill-gap near-misses left to demote.

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
- **Empty `hard_depth_skills`** → everyone clears (FR-5); all availability/location misses are
  near-misses with plain gap wording. Correct: with no hard requirement, the only gaps are the
  negotiable ones.
- **Below-floor edge** — `exact_hard_skill_filter` drops a candidate who holds the skill but below
  `min_proficiency`; they are not in `clears_skills`, so AD-097 **excludes** them from near-misses
  (a below-floor skill is still a non-negotiable gap). Covered by an eval case.
- **Pure hard-skill miss** — a candidate who cleared location + availability but lacks a hard skill
  has reason `HARD_SKILL_MISMATCH`; not in `clears_skills` (the filter only ran on gate misses) →
  excluded from near-misses, recorded in the `exclusion_log`. A no-match where *everyone* fails
  skills → empty near-misses (correct; `reason` + log explain it).
- **Location near-miss with no role city** — wording uses the `"required city"` fallback;
  unaffected.
- **Exclusion without a matching candidate** — already skipped defensively (`by_email.get` →
  `continue`); the pre-filter collection uses the same guard.

## Reuse / no-duplication

`build_near_misses` already imports `exact_hard_skill_filter`; `EligiblePool` was added to the
imports. The verdict is computed by the **same** function the real pipeline uses, so the near-miss
set cannot drift from the gate (Golden rule: one source of truth). No private symbol in
`retrieve.py` is touched, so `dsm/index` is unchanged.

## Eval / test cases (`tests/cli/test_no_match.py`, `tests/cli/test_orchestrator.py`)

1. **FR-1-AC-2** — availability miss, holds all hard skills → near-miss with plain
   `"available N day(s) after deadline"` (no suffix).
2. **FR-1-AC-3** — availability miss, missing a hard skill → **absent** from near-misses.
3. **FR-2-AC-1** — location miss that clears is a near-miss; one that fails skills is absent.
4. **FR-5** — empty `hard_depth_skills` → the gate miss is a near-miss.
5. **FR-7** — pure `HARD_SKILL_MISMATCH` (cleared both gates, lacks a skill) → empty near-misses,
   candidate still in the `exclusion_log` (`test_orchestrator.py`).
6. **FR-6 / regression** — ROLE-03 (all hold java) still yields ordered `[sanjay, meera, arjun]`
   with plain wording; cross-type order + top-3 cap unchanged.
7. **Below-floor** — holds a hard skill below `min_proficiency` → **absent** from near-misses.
8. **Determinism** — same input → identical near-miss list (re-run equality). `selection_rationale`
   is LLM prose, scoped out of the determinism guarantee (like the shortlist narrative).
9. **FR-9-AC-2** — fake recording predictor: a no-match with >3 near-misses invokes it exactly 3×.
10. **FR-9-AC-4** — a predictor that raises → those near-misses returned with
    `selection_rationale=None`; the no-match still succeeds.
11. **FR-10** — the predictor-builder unit test (`tests/match/test_score.py`) asserts the LM sees
    only skills/feedback/role/gap — never `name`/`email` (PII-free by construction).

## Part 3 — closest-on-skills (AD-098)

The mirror of a near-miss: candidates who cleared **both** gates and failed only the hard-skill
filter, surfaced in a **separate, disjoint** `NoMatchResult.closest_on_skills` list. Near-misses
(AD-097) are untouched.

**Modules (additions):**

| File | Change |
|------|--------|
| `dsm/models.py` | `NoMatchResult.closest_on_skills: list[NearMiss] = Field(default_factory=list)` |
| `dsm/index/retrieve.py` | Extract gap computation into a public structured `hard_skill_gap(candidate, hard_skills) -> HardSkillGap \| None`; `_hard_skill_gap` (string) + `exact_hard_skill_filter` reuse it — **no behaviour change** |
| `dsm/cli/commands.py` | New `build_closest_on_skills(...)`; `_no_match` builds + caps + rationale-annotates both lists; `_lineage` dumps the section |

**Data contract** — reuse `NearMiss` (no new model). New structured gap type:

```python
class HardSkillGap(BaseModel, frozen=True):
    missing: list[str]       # hard skills absent by name
    below_floor: list[str]   # held but below floor, e.g. "java (intermediate < expert)"
    @property
    def count(self) -> int: return len(self.missing) + len(self.below_floor)
```

**Algorithm:**

1. **Shared gap helper (FR-12-AC-2)** — refactor `_hard_skill_gap` so the structured missing /
   below-floor computation is public (`hard_skill_gap`); the human string and
   `exact_hard_skill_filter` both call it — identical output, no duplication. (`test_retrieve.py`
   proves the exclusion `detail` wording is unchanged.)
2. **`build_closest_on_skills(candidates, scorecard, exclusion_log)`** — for each
   `HARD_SKILL_MISMATCH` exclusion, build a `NearMiss` with `gap_summary` from `hard_skill_gap`
   (`"missing N hard skill(s): …"` + `"below required proficiency: …"`), `sort_key = (gap.count,
   email)`. Non-`HARD_SKILL_MISMATCH` reasons are skipped (FR-11). Returns the full ordered list;
   caller caps.
3. **`_no_match`** — build + cap **both** lists to 3; when a predictor is injected, reuse
   `explain_near_misses` on each shown set (its prompt already covers a hard-skill gap); construct
   `NoMatchResult(near_misses=…, closest_on_skills=…)`. In the gate-only no-match branch there are
   no `HARD_SKILL_MISMATCH` exclusions → `closest_on_skills` empty (FR-13-AC-2).
4. **`_lineage`** — add a `closest_on_skills` block alongside `near_misses`.

**Edge cases:** gate-only/empty no-match → empty (FR-13-AC-2); below-floor → counted in
`gap.count` + rendered; disjointness holds because each candidate has exactly one exclusion reason
(near-miss = AVAILABILITY/LOCATION, closest = HARD_SKILL).

**Eval cases (add):** ROLE-05-like skill-short candidates appear in `closest_on_skills` (not
`near_misses`) with correct wording; ordering by fewest gaps + cap; double-miss in neither list;
disjointness; rationale on shown ≤3 + skip-on-error; gate-only/empty → empty; below-floor wording;
`hard_skill_gap` unit test; `test_retrieve.py` detail wording unchanged.

## ADRs (in `docs/decision.md`)

**AD-095** (skill verdict — *superseded by AD-097*), **AD-096** (`NearMiss.selection_rationale`
field + LLM seam; AD-060 amendment), **AD-097** (a near-miss must clear hard skills; supersedes
AD-095 + AD-088's near-miss inclusion), and **AD-098** (`NoMatchResult.closest_on_skills`; additive
AD-060 amendment). All cite `specs/b-004-near-miss-skill-verdict/`.
