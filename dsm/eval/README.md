# Eval suite (AD-095/096 + AD-104/105/106)

Three-tier pytest harness proving the 9-step query pipeline keeps its product invariants, plus an AI eval layer for model-quality assessment.

## Tiers

| Tier | What | Runner | Keys? |
|------|------|--------|-------|
| 1 | Six invariant evaluators (gates-respected, hard-skill-not-cleared-by-adjacency, evidence-cited, no-PII-leak, determinism, adjacency-flag) | `tests/eval/test_invariants.py` | No (cassette LM) |
| 2 | Signature regression (clarify/score shape pinning + cassette freshness) | `tests/eval/test_signatures.py` | No (cassette LM) |
| 3 | Live smoke + cassette drift guard | `tests/eval/test_live_smoke.py` | Yes (skipif absent) |

## AI eval layer (AD-104/105/106)

Non-gating model-quality metrics — `make eval` only, never `make check`.

| Component | What | Runner | Keys? |
|-----------|------|--------|-------|
| Golden set | 20+ hand-labelled cases (expected shortlists, relevant sets, faithfulness labels) | `tests/fixtures/golden_set.json` | — |
| Retrieval metrics | Deterministic Recall@K + contextual precision | `tests/eval/test_retrieval_quality.py` | No |
| Faithfulness judge | DeepEval G-Eval narrative faithfulness judge | `tests/eval/test_faithfulness.py` | Yes (OPENROUTER_API_KEY or OPENAI_API_KEY) |

**Golden set status:** Labels are `"draft"` until human sign-off. Judge validation and metric reporting gate on `review_status == "signed_off"`. The judge is adopted only if TPR/TNR >= 80% against signed-off labels.

## Running

```bash
make check       # unit tests + Tier-1 invariant evals (gates every commit)
make eval         # Tiers 1 + 2 + 3 + AI eval layer (live tests skip without keys)
make check-all    # make check + make eval
make eval-record  # re-record cassettes from live LLM
```

## Cassettes

Located under `tests/fixtures/cassettes/<case_id>/`. Keyed by `(case_id, signature, prompt_hash, model_version)`. A key mismatch (prompt changed or model bumped) is a hard error — re-record with `make eval-record`.

## Golden cases (deterministic)

| Case | Fixture | Type | Exercises |
|------|---------|------|-----------|
| ROLE-01 | `tests/fixtures.role_01()` | shortlist | All six invariants |
| ROLE-02 | `tests/fixtures.role_02()` | shortlist | gates-respected, evidence-cited |
| ROLE-03 | `tests/fixtures.role_03()` | no_match | gates-respected, no-PII-leak |

## Golden set (AI eval layer)

`tests/fixtures/golden_set.json` — 21 labelled cases covering base roles, perturbation variants (availability boundary, location relaxation, skill swaps, pool-size extremes), and faithfulness adversarials (fabrication, contradiction, score-inconsistency, evidence-mismatch). See `dsm/eval/golden_set.py` for the typed loader.
