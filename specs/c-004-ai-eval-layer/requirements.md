# Requirements — c-004 AI Eval Layer

> EARS-form acceptance criteria. This slice adds the **model-quality tier** the c-002
> deterministic invariants do not cover: a hand-labelled golden set, a validated
> DeepEval G-Eval faithfulness judge, and retrieval Recall@K / contextual precision.
> Everything here is **non-gating** (`make eval` only, Tier-3-style key-gated
> `skipif`) — the six deterministic invariants stay the commit gate (`make check`).
> References: `ee-query-architecture.md` §9, AD-095/096, `product.md` eval line,
> `docs/decision.md` AD-095 "narrative-faithfulness judge deferred (needs labelled
> data + TPR/TNR ≥ 80% validation)".

---

## Part 1 — Hand-labelled golden set

### R-01 · Golden set structure

**WHEN** the eval harness loads the golden set, the system **SHALL** provide a
data file `tests/fixtures/golden_set.json` containing 20–40 labelled cases, each
with:

- `role_id` — one of the seed roles (ROLE-01/02/03) or a new fixture role.
- `expected_shortlist` — an ordered list of `candidate_id`s representing the
  human-expected ranking (empty for no-match cases).
- `expected_relevant_set` — the full set of `candidate_id`s a correct retrieval
  should surface (for Recall@K; superset of `expected_shortlist`).
- `faithfulness_labels` — per ranked candidate, `faithful: bool` indicating
  whether the narrative is faithful to the cited evidence + candidate data.
  For cases where no narrative exists (no-match), this field is absent.

### R-02 · Golden set loader

**WHEN** `dsm/eval/golden_set.py::load_golden_set()` is called, the system
**SHALL** return a list of typed `GoldenSetCase` objects validated against a
Pydantic model. Malformed entries **SHALL** raise `ValidationError`, never be
silently skipped.

### R-03 · Human label provenance

**WHEN** the golden set is committed, the file **SHALL** carry a top-level
`"_meta"` block recording `labeller`, `label_date`, and `review_status`
(`"draft"` or `"signed_off"`). The eval runner **SHALL** log a warning if
`review_status != "signed_off"` but still run (draft labels are useful during
development; production trust requires sign-off).

### R-04 · Golden set is draft until human sign-off

**WHEN** this slice ships, the golden set **SHALL** have `review_status: "draft"`.
The cases are machine-generated scaffolds with plausible labels based on the
existing fixtures and pipeline behaviour. **HUMAN LABEL SIGN-OFF IS REQUIRED**
before these labels are trusted for judge validation (Part 2) or metric
reporting. The spec, design, and eval runner **SHALL** all state this constraint.

---

## Part 2 — DeepEval G-Eval faithfulness judge

### R-05 · Faithfulness judge implementation

**WHEN** the eval harness runs faithfulness evaluation, the system **SHALL**
use `deepeval.metrics.GEval` configured as a faithfulness judge that scores
whether a candidate narrative follows from the cited evidence + candidate data
(skills, feedback, profile_summary). The judge **SHALL NOT** evaluate objective
properties already covered by the six deterministic invariants (gates, PII,
adjacency, hard-skill-exclusion, citation presence, determinism).

### R-06 · Judge validation against golden labels

**WHEN** the faithfulness judge is run over the golden set's `faithfulness_labels`,
the system **SHALL** compute true-positive rate (TPR) and true-negative rate (TNR)
against the human labels. The judge is **adopted** (test passes) only if
**both TPR ≥ 0.80 and TNR ≥ 0.80**. If the judge fails validation, the test
**SHALL** `pytest.skip` with a message stating the observed TPR/TNR and that the
judge is not yet calibrated — it **SHALL NOT** fail red (an uncalibrated judge is
not a code bug).

### R-07 · Judge validation requires signed-off labels

**WHEN** the golden set has `review_status: "draft"`, the judge validation test
**SHALL** skip with a message: `"golden set not signed off — judge validation
deferred"`. Running a validation pass against draft labels would produce a
meaningless TPR/TNR.

### R-08 · Judge runs under `make eval` only

**WHEN** `make check` runs, the faithfulness judge tests **SHALL NOT** be
collected. The judge is non-deterministic (LLM-based) and requires API keys.
It runs only under `make eval` with the `eval_live` marker and `skipif` when
keys are absent.

### R-09 · No LLM-judging of objective properties

**WHEN** the faithfulness judge evaluates a narrative, the system **SHALL NOT**
re-check whether gates are respected, PII is absent, or citations are present —
those are already covered by the deterministic invariants. The judge's criteria
**SHALL** be limited to: (a) the narrative does not fabricate claims absent from
the evidence, (b) the narrative does not contradict the candidate data, (c) the
narrative's characterisation of skill fit is consistent with the scored
sub-scores.

---

## Part 3 — Retrieval Recall@K / contextual precision

### R-10 · Recall@K metric

**WHEN** the eval harness runs retrieval quality evaluation, the system **SHALL**
compute Recall@K for each golden-set case that has an `expected_relevant_set`,
where K = the configured `index.rerank.top_k` (default 10). Recall@K =
|retrieved ∩ relevant| / |relevant|. The metric is computed over the
post-rerank candidate set (the set that enters scoring), not the final ranked
shortlist.

### R-11 · Contextual precision metric

**WHEN** the eval harness runs retrieval quality evaluation, the system **SHALL**
compute contextual precision: among the top-K retrieved candidates, what fraction
are in the `expected_relevant_set`. This measures whether the retrieval + rerank
pipeline avoids surfacing irrelevant candidates.

### R-12 · DeepEval retrieval metrics (when labels are signed off)

**WHEN** the golden set is signed off, the system **SHALL** additionally run
`deepeval.metrics.ContextualRecallMetric` and
`deepeval.metrics.ContextualPrecisionMetric` over the golden cases, providing
the `expected_output` (from `expected_shortlist`) and the `retrieval_context`
(from the actual retrieved candidates). These provide the LLM-judged
retrieval-quality view alongside the deterministic Recall@K.

### R-13 · Retrieval metrics run under `make eval` only

**WHEN** `make check` runs, retrieval quality tests **SHALL NOT** be collected.
The deterministic Recall@K computation itself needs no keys, but it requires
a populated index (network-dependent or expensive setup); it runs under
`make eval` with the `eval_offline` marker when a test index is available, or
`eval_live` when exercising the real index. The DeepEval LLM-judged retrieval
metrics (R-12) are `eval_live` only.

### R-14 · Hybrid recall must be ON

**WHEN** retrieval metrics are computed, `index.recall.enabled` **SHALL** be
`true` (AD-089). When recall is OFF (exhaustive passthrough), Recall@K is
trivially 1.0 and contextual precision is meaningless — the metrics are
skipped with a message.

---

## Cross-cutting

### R-15 · ADR gate

**WHEN** implementation begins, the system **SHALL** have ratified the
decisions in T-000-ADR (AD-104 through AD-106) into `docs/decision.md`.
Implementation **SHALL NOT** proceed without human sign-off on the ADRs.

### R-16 · No impact on `make check`

**WHEN** `make check` runs, **no** tests from this slice **SHALL** be
collected. The six deterministic invariants + Tier-1/2 evals stay exactly as
they are. This slice adds only to `make eval`.

### R-17 · `docs/tech.md` updated

**WHEN** the slice is complete, `docs/tech.md` §Eval **SHALL** reflect the
AI eval layer: golden set, validated faithfulness judge, retrieval metrics.
The "narrative-faithfulness judge deferred" note is replaced with the adopted
(or pending-validation) status.

### R-18 · Lane file updated

**WHEN** the slice is complete, `docs/progress.C.md` **SHALL** be updated
via `/handoff`, and `docs/decision.md` **SHALL** carry AD-104 through AD-106.
