# Tasks — c-002 Query-Time Eval Harness

> Ordered, atomic, independently testable. One task = one commit, imperative, referencing
> the spec. First task is T-000-ADR — **STOP for sign-off before any code.**

---

## T-000-ADR — Ratify AD-095/094 (GATE — stop for human sign-off)

- Append AD-095 (eval harness architecture: code-based-first + promptfoo resolution) and
  AD-096 (`make check` vs `make check-all` split with exact marker/collection rules) to
  `docs/decision.md`. Next IDs start at AD-095 (verified: `decision.md` ends at AD-092).
- Register pytest markers `eval_offline` and `eval_live` in `pyproject.toml`
  `[tool.pytest.ini_options]`.
- Drop `promptfoo` from `pyproject.toml` dev dependencies (AD-095 option a). Keep
  `deepeval` available.
- Update `docs/tech.md` §Eval to reflect the three-tier model (drop "Promptfoo
  (signature-level)"; describe Tier 1/2/3).
- `make check` GREEN.
- **STOP — human sign-off before proceeding.**

**AC:** AD-095/094 in `decision.md`; markers registered; `promptfoo` removed; `tech.md`
updated; `make check` green.

---

## T-001 — Enrich seed fixtures with source evidence

- Add optional `profile_summary` and `feedback_entries` params to the `_candidate` helper
  in `tests/fixtures/__init__.py`.
- Add hand-authored `profile_summary` and `FeedbackEntry` items to ROLE-01/02/03
  candidates per the design §"Fixture enrichment plan".
- **ROLE-01 extension:** add candidate Suresh (`skill="java"`, adjacent to required `kotlin`)
  who gets excluded by `exact_hard_skill_filter` with `HARD_SKILL_MISMATCH`.
- **ROLE-01 scorecard:** add `desired_skills=[SkillRequirement(name="java", depth=DESIRED)]`
  so `adjacency_flag` invariant is exercised non-vacuously.
- Ensure the enriched text contains exact phrases suitable for citation verification.
- Verify: existing gates/rank tests still pass (`make check` green). The new candidate
  and desired skills are additive — existing gate/rank tests that use `role_01()` will see
  one more excluded candidate and the desired skill, which doesn't change gate/rank outcomes
  for the previously-passing candidates.

**AC:** R-14. Fixtures enriched; `profile_summary` and `feedback.entries` populated;
Suresh added for hard-skill-adjacency testing; desired skills added for adjacency-flag
testing; existing tests green.

---

## T-002 — Implement `InvariantResult` + six invariant evaluators

- Create `dsm/eval/invariants.py` with `InvariantResult` dataclass and six pure functions:
  `gates_respected`, `hard_skill_not_cleared_by_adjacency`, `evidence_cited`, `no_pii_leak`,
  `determinism`, `adjacency_flag`.
- Each function takes a pipeline result + context, returns `InvariantResult(passed, reason)`.
- `no_pii_leak` docstring states the structural-only limitation (stub anonymiser).
- `determinism` docstring states it does not test live-model reproducibility.
- Reuse `dsm/pii/leakscan.assert_no_leak` for `no_pii_leak`. Reimplement `_norm` for
  `evidence_cited` (don't import from `dsm/match/score`).
- No test-framework imports in the module.
- `make check` GREEN (module compiles, imports clean).

**AC:** R-01 through R-06 (evaluator signatures). Six functions importable; each returns
`InvariantResult`. Docstrings document limitations.

---

## T-003 — Golden cases + cassette LM

- Create `dsm/eval/cases.py` with `CassetteKey`, `GoldenCase`, `CassetteLM`,
  `pseudonymise_candidates`, `load_golden_cases`, `load_cassette`,
  `validate_cassette_freshness`.
- `pseudonymise_candidates` mirrors `GoldCandidateStore`: replaces `email`/`name` with
  `candidate_id` and returns the original names+emails as a `known_pii` list for
  leak-scanning. Golden cases call this before feeding candidates to `run_match`.
- `CassetteLM` produces two separate predict callables (`clarify_predict` →
  `ClarifyPredictor`, `score_predict` → `ScorePredictor`) that replay recorded responses.
- Create `tests/fixtures/cassettes/` directory structure with initial cassette JSON files
  for ROLE-01/02/03 + negative case. Hand-author the cassette `clarify`/`score` responses
  to match the enriched fixtures (evidence texts are exact substrings of source).
- Cassette key includes `(case_id, signature, prompt_hash, model_version)`.
  `prompt_hash` is the sha256 of the DSPy Signature class source (docstring + fields).
- Create `SeamInputs` dataclass and wrapping predictors that capture seam inputs.
- `make check` GREEN.

**AC:** R-07, R-08. Golden cases loadable; candidates pseudonymised to match real pipeline;
cassettes checked in; `CassetteLM` replays correctly; stale key → error (not skip/fallback).

---

## T-004 — `tests/eval/conftest.py` live-provider guard

- Create `tests/eval/conftest.py` with autouse fixture that patches `ModalEmbedClient`
  and the DSPy LM constructor to raise `RuntimeError` in `eval_offline` tests.
- Create `_has_keys()` helper for `eval_live` skipif.
- Create `tests/eval/__init__.py`.
- `make check` GREEN.

**AC:** R-12. Guard patches live providers; a test (in T-005) proves it fires.

---

## T-005 — Tier-1 runner (`tests/eval/test_invariants.py`)

- Create `tests/eval/test_invariants.py` with `@pytest.mark.eval_offline` tests.
- For each golden case (ROLE-01/02/03 + negative): drive `run_match` with cassette LM
  (via the injected `predict` seam), then assert all six invariants pass.
- For each invariant: add a deliberately-failing test with a tampered `ShortlistResult`
  (per the design's worked examples). Assert `passed=False` with a descriptive reason.
- Add a test that proves the conftest guard fires (instantiating `ModalEmbedClient` in an
  `eval_offline` test raises `RuntimeError`).
- `determinism` test: shuffle candidate order N times, assert byte-identical output.
- `make check` GREEN (Tier-1 evals now included in `make check`).

**AC:** R-01–R-06, R-09, R-12, R-15. All six invariants × golden cases pass; each has
a deliberately-failing fixture; guard test fires; `make check` collects Tier-1.

---

## T-006 — Tier-2 signature regression (`tests/eval/test_signatures.py`)

- Create `tests/eval/test_signatures.py` with `@pytest.mark.eval_offline` tests.
- Pin `clarify` and `score` DSPy signatures against cassette inputs:
  - `clarify`: output is a valid `ScorecardClarification`.
  - `score`: sub-scores ∈ [0,1], at least one citation present, hard skill never credited
    via adjacency in the raw `ScoreExtraction`.
- `make check` GREEN (signatures collected under `eval_offline` in `make test`).

**AC:** R-10. Signature regression pins well-formedness.

---

## T-007 — Tier-3 live smoke + drift guard (`tests/eval/test_live_smoke.py`)

- Create `tests/eval/test_live_smoke.py` with `@pytest.mark.eval_live` tests.
- Live smoke: one real-LLM pass over ROLE-01 (enriched), assert well-formed
  `ShortlistResult` (ranked_assessments non-empty, scores in range, no validation errors).
- Drift guard: re-record cassettes into a temp dir, diff against committed cassettes.
  Flag (warn, not fail) when live output has drifted significantly.
- Both tests carry `@pytest.mark.skipif(not _has_keys(), reason=...)`.
- `make check` GREEN (live tests skipped without keys); `make eval` runs them with keys.

**AC:** R-11. Live smoke runs with keys, skips without. Drift guard flags divergence.

---

## T-008 — Wire `make eval` + `make check-all` in Makefile

- Replace the `eval` target stub (`echo SKIP; exit 1`) with
  `uv run pytest tests/eval -m "eval_offline or eval_live" -v`.
- Add `eval-record` target: `uv run python -m dsm.eval.record`.
- Add `eval-tier1` target: `uv run pytest tests/eval/test_invariants.py -v`.
- Update `test` target to `uv run pytest tests/ --ignore=tests/eval -v` (unit tests
  only, eval tests excluded).
- Update `check` target: `format lint typecheck test eval-tier1 imports` (adds `eval-tier1`
  so Tier-1 invariant evals gate every commit, but Tier-2 signature regression is not
  pulled in).
- `check-all: check eval` (unchanged structure, but `eval` now works).
- Verify: `make check` collects unit tests + Tier-1 only; `make eval` collects Tiers 1+2+3;
  `make check-all` runs both.
- `make check` GREEN; `make eval` no longer exits 1.

**AC:** R-13. `make eval` runs; `make check` includes Tier-1 only (not Tier-2);
`make check-all` is the gate. Marker collection matches AD-096.

---

## T-009 — Cassette recorder (`dsm/eval/record.py`)

- Create `dsm/eval/record.py` as a `__main__`-runnable module.
- For each golden case: run the live LM (`clarify_role` + `score_candidate` with real
  OpenRouter predictor) and write `{clarify,score}.json` cassettes with the correct key.
- Print a summary of recorded/updated cassettes.
- Skip gracefully if API keys are absent.
- `make check` GREEN (module compiles; no test needed — it's a dev tool).

**AC:** R-08. `make eval-record` regenerates cassettes explicitly.

---

## T-010 — Update `dsm/eval/README.md` + docs

- Replace the "not configured" placeholder in `dsm/eval/README.md` with the tier model +
  how to run (`make check` for Tier-1, `make eval` for all, `make eval-record` to
  regenerate cassettes).
- Verify `docs/decision.md` carries AD-095/094 (done in T-000).
- `make check` GREEN.

**AC:** R-18 (partial). README updated.

---

## T-011 — `/handoff` — update `docs/progress.C.md`

- Run `/handoff` to update `docs/progress.C.md` with the C-2 session log entry.
- Verify all acceptance criteria from `requirements.md` are met.
- `make check` GREEN; `make check-all` GREEN (or eval_live tests skip cleanly).

**AC:** R-18. Lane file current; slice complete.

---

## Task dependency graph

```
T-000-ADR (gate — STOP)
    │
    ├── T-001 (enrich fixtures + Suresh + desired skills)
    │       │
    │       └── T-002 (invariant evaluators)
    │               │
    │               └── T-003 (golden cases + cassette LM + pseudonymise)
    │                       │
    │                       ├── T-004 (conftest guard)
    │                       │       │
    │                       │       └── T-005 (Tier-1 runner)
    │                       │
    │                       ├── T-006 (Tier-2 signatures)
    │                       │
    │                       ├── T-007 (Tier-3 live smoke)
    │                       │
    │                       └── T-009 (cassette recorder — needs T-003 GoldenCase defs)
    │
    └── T-008 (Makefile wiring)  ← can start after T-000; finalized after T-005/06/07
            │
            └── T-010 (README + docs)
                    │
                    └── T-011 (/handoff)
```
