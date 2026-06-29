# C-007 Query-Side Negation → Hard Location Filter — Design

> Implements `requirements.md`. The negation is a **typed exclusion enforced in the deterministic
> location gate** — never the embedding (the A2 invariant). Extends the c-006 NL front door; the
> CSV path is untouched (defaults empty).

---

## 1. Data flow

```
prose "…, not Chennai, …"
  → intake LLM → RoleIntake.exclude_cities = ["Chennai"]        (match-local; proposed)
  → assemble_role → OpenRole.exclude_cities = {"chennai"}        (frozen field; normalised)
  → echo "excludes: chennai" + confirm                          (human reviews the gate input)
  → clarify_role → TargetProfileScorecard.exclude_cities = {…}   (threaded, not LLM-set)
  → filter_candidates → _location_passes: candidate.city ∈ exclude_cities → EXCLUDE  (pure Python)
```
The exclusion reaches **only** the gate. Recall/rerank/embedding never see it (FR-3-AC-4).

## 2. Modules touched

| Module | Change |
|--------|--------|
| `dsm/models.py` | **Frozen amendment (AD-XXX):** add `exclude_cities: frozenset[str] = frozenset()` to `OpenRole` **and** `TargetProfileScorecard`. Regenerate snapshot (`make contract-snapshot`). |
| `dsm/match/gates.py` | `_location_passes`: exclude when `candidate.location.city` ∈ `scorecard.exclude_cities` (casefold), **first**, regardless of co-location. `filter_candidates`: re-check the exclusion to pick the `LOCATION_MISMATCH` detail wording (the gate returns a bool). |
| `dsm/cli/commands.py::build_near_misses` | **Skip** `LOCATION_MISMATCH` rows whose candidate home city ∈ `scorecard.exclude_cities` — an exclusion is non-negotiable, never a near-miss (FR-3-AC-5). |
| `dsm/match/clarify.py` | `_echo` + the LLM-refine branch carry `exclude_cities` from role → scorecard. |
| `dsm/match/models.py` | `RoleIntake`: add `exclude_cities: list[str] = []` (match-local, not frozen). |
| `dsm/match/intake.py` | `assemble_role`: normalise `RoleIntake.exclude_cities` → `OpenRole.exclude_cities` (lowercased frozenset). |
| `dsm/cli/commands.py` | `_echo_role`: show `excludes: …` line. |
| `config/prompts/role_intake.md` | Instruct: negation → `exclude_cities`, never a positive `location_city`. |
| `dsm/match/demand.py` | **No change** — CSV `OpenRole` gets the default empty set (FR-1-AC-3). |

## 3. Contract amendment (`dsm/models.py`)

```python
class OpenRole(BaseModel, frozen=True):
    ...
    location: Location
    co_location_required: bool
    exclude_cities: frozenset[str] = frozenset()   # AD-XXX: query-side negation; gate-enforced
    start_date: date
    ...

class TargetProfileScorecard(BaseModel, frozen=True):
    ...
    co_location_required: bool
    exclude_cities: frozenset[str] = frozenset()   # AD-XXX: threaded from OpenRole by clarify
    start_date: date
    ...
```
Both default to `frozenset()` → additive/backwards-compatible. `frozenset` keeps the model hashable
+ frozen-consistent (mirrors `Location.onsite_cities`, AD-086). Run `make contract-snapshot`; the
diff is the ADR-backed reviewable change (FR-1-AC-2).

## 4. The gate (`dsm/match/gates.py::_location_passes`)

Prepend the exclusion check (the only change):
```python
def _location_passes(candidate, scorecard) -> bool:
    # AD-XXX query-side negation: a candidate based in an excluded city never passes — checked
    # first, independent of co_location (the role doesn't want a person from that city at all).
    cand_city = (candidate.location.city or "").strip().casefold()
    if cand_city and cand_city in {c.strip().casefold() for c in scorecard.exclude_cities}:
        return False
    # … existing positive onsite/distributed logic unchanged …
```
- Matches on **home `city`** only — `onsite_cities` (willingness) is not consulted (FR-3-AC-2).
- Empty set → the comprehension is empty, `in` is always False → **byte-identical** to today (FR-3-AC-3).
- Placed **first**, before the `if not co_location_required: return country == country` distributed
  early-return, so an excluded city is removed even for a distributed role (FR-3-AC-1).
- Pure Python; `gates.py` still imports nothing from `pii`/`index`/LLM (the import contract holds).
- The exclusion detail in `filter_candidates`: `_location_passes` returns a bool, so `filter_candidates`
  **re-checks** `candidate.city ∈ exclude_cities` to choose the `LOCATION_MISMATCH` `Exclusion.detail`
  ("candidate is in `<city>`, which the role excludes" vs the existing positive-miss wording). Reason
  enum unchanged — still `LOCATION_MISMATCH` (FR-3-AC-6).

### 4a. Near-miss exclusion (`dsm/cli/commands.py::build_near_misses`) — FR-3-AC-5

`build_near_misses` treats `LOCATION_MISMATCH` as a **negotiable** gate (a candidate "one decision
away"), re-runs the hard-skill filter, and surfaces clearers as near-misses. An excluded-city
candidate must **not** be re-admitted that way — an exclusion is deliberate, not negotiable. Add a
skip: when building near-misses, drop any `LOCATION_MISMATCH` row whose candidate home city ∈
`scorecard.exclude_cities`. (Without this, a Chennai-based Kotlin engineer excluded by "not Chennai"
re-appears in `no_match.near_misses` framed as "one decision away" — the c-006 hard-exclude-leaks-back
pattern.) Regression test in `tests/cli/test_match_query.py`.

## 5. NL intake

- `RoleIntake.exclude_cities: list[str] = Field(default_factory=list)` (match-local).
- `assemble_role`: `exclude_cities = frozenset(c.strip().lower() for c in intake.exclude_cities if c.strip())`
  → `OpenRole(..., exclude_cities=exclude_cities)`. A negated city is **not** also a positive
  `location_city` (the prompt enforces; assembly trusts the buckets).
- **"Anywhere but X" must not demand a location (FR-4-AC-1/1b).** c-006's `_resolve_location` returns
  *missing* when there is no `location_city` and not `remote_within_country` → a location clarification.
  An exclusion-only query ("not Chennai", no positive city) is a **distributed** role, not a missing
  location. Fix: `_resolve_location` (or `assemble_role`) treats *no city + not remote + non-empty
  `exclude_cities`* as a **valid distributed location** — `Location(city=None)`, and the
  Python-derived `co_location_required = bool(location_city) and not remote_within_country` is then
  `False` (distributed). So the role gates as "any India location, minus the excluded cities". Missing
  location still fires only when no city **and** not remote **and** `exclude_cities` empty.
- `config/prompts/role_intake.md`: add a rule — "A negation ('not X', 'anywhere but X', 'exclude X',
  'no one from X') goes in `exclude_cities` (lowercased), **never** in `location_city`, and is not
  duplicated into `notes`. A plain city stays `location_city`." Keep the existing never-guess directive.
- `_echo_role`: add `  excludes       : chennai` (only when non-empty), so the human confirms the
  gate input (FR-4-AC-4).

## 6. Edge cases

| Case | Handling |
|------|----------|
| "Anywhere but X" ("not Chennai", no positive city) | **Distributed** role: `location.city=None`, `co_location_required=False`, `exclude_cities={"chennai"}` — assembles cleanly, **no** location clarification (FR-4-AC-1). Gates as any-India minus Chennai. |
| Positive + negative ("in Bengaluru, not Chennai") | `location.city="Bengaluru"`, `exclude_cities={"chennai"}` — both honoured; gate excludes Chennai-based, requires Bengaluru (onsite) / India (distributed). |
| Self-contradiction ("in Chennai but not Chennai") | Positive gate needs Chennai, exclusion fails Chennai → empty pool. Echo shows both → human catches it; not the parser's job to reconcile. **Optional guard:** if `location_city` ∈ `exclude_cities`, surface a one-line warning in the echo (cheap; flagged, not auto-resolved). |
| Candidate based elsewhere but open to onsite in an excluded city | **Passes** the exclusion (home city ≠ excluded); FR-3-AC-2. |
| Remote candidate (`city=None`) under an exclusion | `cand_city==""` → exclusion can't fire → falls to the normal positive logic. |
| Exclude a city not present in any candidate | No-op; no candidate excluded by it. |
| Excluded candidate that clears hard skills (no-match path) | **Not** surfaced as a near-miss — `build_near_misses` skips it (FR-3-AC-5). |

## 7. Eval / tests

- **`tests/match/test_gates.py`** (in `make check`): excluded home city → `LOCATION_MISMATCH` (onsite **and** distributed role); `onsite_cities` willingness not triggered; empty default unchanged.
- **`tests/match/test_intake.py`**: negation → `exclude_cities`; "anywhere but X" (no positive city) → distributed role, **not** `ClarificationNeeded`; positive+negative coexist; lowercased; absent → empty.
- **`tests/match/test_clarify.py`**: `exclude_cities` threaded through echo + LLM-refine paths; clarify LLM cannot set it.
- **`tests/cli/test_match_query.py`**: `--query "… not Chennai"` → echo shows `excludes`, a Chennai candidate is gated out of the shortlist, **and** an excluded Chennai candidate that clears hard skills is **absent** from `no_match.near_misses` (FR-3-AC-5 regression).
- **NL eval** (`tests/fixtures/nl_intake_golden.json` + `test_nl_intake.py`): add a negation golden case (offline replay + live), asserting `exclude_cities`.
- Frozen-contract: `make contract-snapshot` regenerated; `tests/docs` green.

## 8. ADR (placeholder — `/handoff-index` assigns at merge; footer next AD-112)

- **AD-XXX · Query-side location negation via `exclude_cities` (frozen-contract amendment; gate-enforced)** — Add `exclude_cities: frozenset[str] = frozenset()` to `OpenRole` + `TargetProfileScorecard` (additive/defaulted, `make contract-snapshot`). NL intake parses "not <city>" into it (`RoleIntake.exclude_cities`, match-local), `assemble_role` normalises it, `clarify_role` threads it to the scorecard, and the **pure-Python location gate** excludes a candidate whose **home city** is in the set — before the positive logic, regardless of co-location, **never** via the embedding (the A2 invariant: negation is a hard filter, not cosine). The LLM only *proposes* the set (a parse value, confirmation-echoed — an LLM-parsed *fact* like `location_city`, not a policy inference, so it does not repeat the c-006 co-location blocker); eligibility stays deterministic + LLM-free (AD-002). An exclusion is **non-negotiable** — `build_near_misses` skips excluded candidates so they never re-surface as "one decision away". An exclusion with **no positive city** ("anywhere but X") assembles as a **distributed** role, not a missing-location clarification. Reason enum unchanged (`LOCATION_MISMATCH`); `filter_candidates` re-checks the exclusion only to pick the `detail` wording. Scope: cities only — sectors / candidate-level / skill negation deferred to their own ADRs. Rejected: routing negation into the recall/embedding query (the exact failure A2 names); a new `ExclusionReason` (a city exclusion *is* a location mismatch); the LLM setting the exclusion as a gate decision (it proposes; Python + the echo gate it). See `specs/c-007-query-negation/`.
