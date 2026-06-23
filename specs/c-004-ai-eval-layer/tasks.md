# Tasks — c-004 AI Eval Layer

> Ordered, atomic, independently testable. One task = one commit, imperative,
> referencing the spec. First task is T-000-ADR — **STOP for sign-off before
> any code.**

---

## T-000-ADR — Ratify AD-104/105/106 (GATE — stop for human sign-off)

- Append AD-104 (hand-labelled golden set), AD-105 (DeepEval G-Eval
  faithfulness judge, validated, non-gating), and AD-106 (retrieval Recall@K +
  contextual precision, non-gating) to `docs/decision.md`. Next IDs start at
  AD-104 (verified: `decision.md` ends at AD-103).
- Update `docs/tech.md` §Eval to reflect the AI eval layer: replace
  "Narrative-faithfulness judge deferred (needs labelled validation data)" with
  "Narrative-faithfulness G-Eval judge wired (AD-105), pending golden-set
  sign-off for validation. Retrieval Recall@K + contextual precision wired
  (AD-106)."
- `make check` GREEN (no code changes; docs only).
- **STOP — human sign-off before proceeding.**

**AC:** AD-104/105/106 in `decision.md`; `tech.md` updated; `make check` green.

---

## T-001 — Golden set loader (`dsm/eval/golden_set.py`)

- Create `dsm/eval/golden_set.py` with Pydantic models: `GoldenSetMeta`,
  `GoldenSetCase`, `GoldenSet`.
- Implement `load_golden_set(path: Path | None = None) -> GoldenSet` that loads
  from `tests/fixtures/golden_set.json` (default) or a provided path.
- Malformed entries raise `ValidationError`. Missing file raises `FileNotFoundError`.
- `GoldenSet.is_signed_off` property checks `meta.review_status == "signed_off"`.
- `make check` GREEN (module compiles, imports clean).

**AC:** R-02, R-03. Typed loader importable; validation errors on malformed input.

---

## T-002 — Draft golden set (`tests/fixtures/golden_set.json`)

- Create `tests/fixtures/golden_set.json` with 20–40 labelled cases covering:
  - **Base cases:** ROLE-01 (shortlist, 4 ranked), ROLE-02 (shortlist, 3 ranked),
    ROLE-03 (no-match, 4 excluded).
  - **Perturbation variants:** availability shifts, skill swaps, location changes,
    pool-size variations for each base role (~12–15 cases).
  - **Faithfulness adversarials:** fabricated narratives, contradictions,
    score-inconsistencies (~5 cases with `faithfulness_labels: {cid: false}`).
- Each case carries `expected_shortlist` (ordered candidate_ids),
  `expected_relevant_set` (full relevant set for Recall@K), and
  `faithfulness_labels` (per-candidate faithful: bool).
- `_meta.review_status: "draft"`. `_meta.labeller: "machine-draft (pipeline + fixture analysis)"`.
- Candidate IDs use the `candidate_id(email)` HMAC derivation from the fixtures
  (deterministic, test-stable).
- Add new fixture roles (ROLE-04 through ROLE-06) to `tests/fixtures/__init__.py`
  as needed for perturbation and edge-case coverage.
- Verify: `load_golden_set()` loads all cases without validation error.
- `make check` GREEN (no eval tests added yet; fixtures are additive).

**AC:** R-01, R-03, R-04. Golden set file committed with ≥20 cases; draft status;
new fixture roles added; loader validates all cases.

---

## T-003 — Deterministic retrieval metrics (`dsm/eval/retrieval_quality.py`)

- Create `dsm/eval/retrieval_quality.py` with `RetrievalMetrics` dataclass and
  pure functions: `compute_recall_at_k`, `compute_contextual_precision`,
  `compute_retrieval_metrics`.
- All functions are deterministic, no LLM, no keys.
- Edge cases: empty relevant set → recall = 1.0 (vacuous); empty retrieved → precision = 0.0.
- `make check` GREEN (module compiles, imports clean).

**AC:** R-10, R-11 (computation logic). Pure functions, tested in T-005.

---

## T-004 — Faithfulness judge (`dsm/eval/faithfulness.py`)

- Create `dsm/eval/faithfulness.py` with `FaithfulnessVerdict`, `JudgeValidation`
  dataclasses and functions: `build_faithfulness_judge`, `judge_narrative`,
  `validate_judge`.
- `FAITHFULNESS_CRITERIA` string explicitly excludes the six deterministic
  invariant concerns.
- `build_faithfulness_judge()` returns a configured `deepeval.metrics.GEval`.
- `judge_narrative()` runs the judge on a single narrative + context.
- `validate_judge()` computes TPR/TNR from predictions vs human labels.
- `make check` GREEN (module compiles; no DeepEval import at `make check` time
  — the import is inside the functions, not at module top level, so offline
  tests never trigger it).

**AC:** R-05, R-09. Judge module importable; criteria exclude deterministic concerns.

---

## T-005 — Retrieval quality tests (`tests/eval/test_retrieval_quality.py`)

- Create `tests/eval/test_retrieval_quality.py`.
- **`eval_offline` tests** (deterministic, no keys):
  - `test_recall_perfect`: all relevant retrieved → 1.0.
  - `test_recall_partial`: some relevant missed → < 1.0 (exact value asserted).
  - `test_recall_empty_relevant`: vacuous case → 1.0.
  - `test_precision_perfect`: all retrieved are relevant → 1.0.
  - `test_precision_degraded`: irrelevant candidates in top-K → < 1.0.
  - `test_precision_empty_retrieved`: edge case → 0.0.
  - `test_skipped_when_recall_off`: mock config with `recall.enabled=false` →
    `pytest.skip`.
- **`eval_live` tests** (DeepEval metrics, key-gated):
  - `test_deepeval_contextual_recall`: run `ContextualRecallMetric` over golden
    cases (skip if draft labels).
  - `test_deepeval_contextual_precision`: run `ContextualPrecisionMetric` over
    golden cases (skip if draft labels).
- `make check` GREEN (no `eval_offline`/`eval_live` tests collected by `make check`).
- `make eval` collects and runs the `eval_offline` tests; `eval_live` tests
  skip without keys.

**AC:** R-10, R-11, R-12, R-13, R-14, R-16.

---

## T-006 — Faithfulness judge tests (`tests/eval/test_faithfulness.py`)

- Create `tests/eval/test_faithfulness.py`.
- **`eval_live` tests** (all key-gated `skipif`):
  - `test_judge_faithful_narrative`: run judge on a known-faithful narrative
    (from golden set), assert score ≥ threshold.
  - `test_judge_fabricated_narrative`: run judge on a tampered narrative
    (fabricated claim), assert score < threshold.
  - `test_judge_contradictory_narrative`: run judge on a narrative that
    contradicts candidate data, assert score < threshold.
  - `test_validate_tpr_tnr_draft_skips`: when `review_status == "draft"`,
    skip with message "golden set not signed off".
  - `test_validate_tpr_tnr_signed_off`: when `review_status == "signed_off"`,
    compute TPR/TNR, skip (not fail) if below 0.80 with observed values in
    the skip message.
- `make check` GREEN (`eval_live` not collected).
- `make eval` collects; tests skip without keys or with draft labels.

**AC:** R-05, R-06, R-07, R-08, R-09, R-16.

---

## T-007 — Wire `make eval` collection + update docs

- Verify that `make eval` collects the new test files
  (`test_faithfulness.py`, `test_retrieval_quality.py`) via the existing
  `tests/eval/ -m "eval_offline or eval_live"` collection rule. No Makefile
  changes should be needed (the existing `eval` target already collects all of
  `tests/eval/`).
- Update `dsm/eval/README.md` to describe the AI eval layer (golden set,
  faithfulness judge, retrieval metrics) alongside the existing tier model.
- Update `docs/tech.md` §Eval if not already done in T-000.
- `make check` GREEN; `make eval` GREEN (new `eval_offline` tests pass;
  `eval_live` tests skip without keys).

**AC:** R-16, R-17.

---

## T-008 — `/handoff` — update `docs/progress.C.md`

- Run `/handoff` to update `docs/progress.C.md` with the c-004 session log
  entry.
- Verify all acceptance criteria from `requirements.md` are met.
- `make check` GREEN; `make eval` GREEN.

**AC:** R-18. Lane file current; slice complete.

---

## Task dependency graph

```
T-000-ADR (gate — STOP)
    │
    ├── T-001 (golden set loader)
    │       │
    │       └── T-002 (draft golden set + new fixture roles)
    │               │
    │               ├── T-003 (deterministic retrieval metrics)
    │               │       │
    │               │       └── T-005 (retrieval quality tests)
    │               │
    │               └── T-004 (faithfulness judge module)
    │                       │
    │                       └── T-006 (faithfulness judge tests)
    │
    └── T-007 (wire make eval + docs)  ← after T-005/T-006
            │
            └── T-008 (/handoff)
```
