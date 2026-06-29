# C-007 Query-Side Negation — Tasks

> **Lane:** C · **Slice:** C-007 (workshop A2). One task = one commit; `make check` green after each.
> Branch: `feat/c/007-query-negation`. **T-000 STOPs for sign-off** (frozen-contract amendment).

---

## T-000 · ADR sign-off gate (frozen contract)

Record **AD-XXX** (placeholder) in `docs/decision.md`: query-side location negation via
`exclude_cities` on `OpenRole` + `TargetProfileScorecard` (frozen-contract amendment, AD-060),
gate-enforced in pure Python, never via the embedding (the A2 invariant). Confirm the open
questions (scope = cities only; exclude on home city; amend both models).

**This is a frozen-contract amendment + a gate change → STOP for human sign-off before code
(golden rule 1 + "stop and ask when a change touches a gate").**

**Acceptance:** AD-XXX recorded, cross-refs to AD-002/060/086/110; footer/index AD-range will be
reconciled at merge via `/handoff-index`.

---

## T-001 · Frozen-contract amendment + snapshot

- `dsm/models.py`: add `exclude_cities: frozenset[str] = frozenset()` to `OpenRole` and
  `TargetProfileScorecard` (design §3).
- `make contract-snapshot` to regenerate the baseline; `tests/test_models.py` covers the default.

**Acceptance:** `make check` GREEN (frozen-contract test passes against the regenerated snapshot);
both models accept/omit `exclude_cities` (FR-1).

---

## T-002 · Gate enforcement + near-miss skip + clarify thread-through

- `dsm/match/gates.py::_location_passes`: prepend the home-city exclusion check (design §4, **first**,
  before the distributed early-return); `filter_candidates` re-checks `candidate.city ∈ exclude_cities`
  to pick the `LOCATION_MISMATCH` detail wording (FR-3-AC-6).
- `dsm/cli/commands.py::build_near_misses`: **skip** `LOCATION_MISMATCH` rows whose candidate home
  city ∈ `scorecard.exclude_cities` (FR-3-AC-5 — an exclusion is never a near-miss; design §4a).
- `dsm/match/clarify.py`: thread `exclude_cities` role → scorecard in both `_echo` and the
  LLM-refine branch.
- Tests: `tests/match/test_gates.py` (excluded home city on onsite **and** distributed; `onsite_cities`
  not triggered; empty default byte-identical) + `tests/match/test_clarify.py` (threaded; LLM can't
  set it) + `tests/cli/test_match_query.py` regression (excluded skill-clearing candidate absent from
  `near_misses`).

**Acceptance:** `make check` GREEN; FR-2 + FR-3 (incl. AC-5/AC-6) covered; `gates.py` import-clean (no LLM/embedding).

---

## T-003 · NL intake parsing + echo

- `dsm/match/models.py`: `RoleIntake.exclude_cities: list[str] = []`.
- `dsm/match/intake.py::assemble_role`: normalise → `OpenRole.exclude_cities` (lowercased frozenset);
  a negated city is never a positive `location_city`. **"Anywhere but X" fix:** no positive city +
  not remote + non-empty `exclude_cities` → a **distributed** `Location(city=None)` (co_location
  False), **not** a missing-location `ClarificationNeeded` (FR-4-AC-1/1b; design §5).
- `config/prompts/role_intake.md`: negation → `exclude_cities`, never `location_city` (keep never-guess).
- `dsm/cli/commands.py::_echo_role`: show `excludes: …` when non-empty.
- Tests: `tests/match/test_intake.py` (negation → set; positive+negative coexist; lowercased; absent →
  empty) + `tests/cli/test_match_query.py` (`--query "… not Chennai"` → echo shows it + Chennai
  candidate gated out).

**Acceptance:** `make check` GREEN; FR-4 covered.

---

## T-004 · NL-intake negation eval case + docs/lane

- Add a negation golden case to `tests/fixtures/nl_intake_golden.json` (prose with "not Chennai" →
  `recorded_intake.exclude_cities` + `expected`); `test_nl_intake.py` already iterates cases, so
  the offline tier covers it and a `live: true` flag exercises the live tier. Extend the typed
  loader's `NLIntakeExpected` + the offline/live assertions to check `exclude_cities`.
- `README.md`: note `--query` supports "not <city>" exclusions (cite behaviour, not values).
- `docs/progress.C.md`: `/handoff` session-log + Next-up.

**Acceptance:** `make eval` GREEN (negation case offline; live with keys); `make check` GREEN; spec
acceptance criteria met.

---

## Task → acceptance-criterion map

| Task | Covers |
|------|--------|
| T-000 | ADR gate (frozen-contract sign-off) |
| T-001 | FR-1 (all), NF-2 |
| T-002 | FR-2 (all), FR-3 (all, incl. AC-5 near-miss skip + AC-6 detail), NF-3 |
| T-003 | FR-4 (all, incl. AC-1/1b "anywhere but X" → distributed), NF-1/NF-4 |
| T-004 | DoD eval + docs/lane |
