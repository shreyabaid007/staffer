# Eval suite (AD-093 / AD-094)

Three-tier pytest harness proving the 9-step query pipeline keeps its product invariants.

## Tiers

| Tier | What | Runner | Keys? |
|------|------|--------|-------|
| 1 | Six invariant evaluators (gates-respected, hard-skill-not-cleared-by-adjacency, evidence-cited, no-PII-leak, determinism, adjacency-flag) | `tests/eval/test_invariants.py` | No (cassette LM) |
| 2 | Signature regression (clarify/score shape pinning + cassette freshness) | `tests/eval/test_signatures.py` | No (cassette LM) |
| 3 | Live smoke + cassette drift guard | `tests/eval/test_live_smoke.py` | Yes (skipif absent) |

## Running

```bash
make check       # unit tests + Tier-1 invariant evals (gates every commit)
make eval         # Tiers 1 + 2 + 3 (live tests skip without keys)
make check-all    # make check + make eval
make eval-record  # re-record cassettes from live LLM
```

## Cassettes

Located under `tests/fixtures/cassettes/<case_id>/`. Keyed by `(case_id, signature, prompt_hash, model_version)`. A key mismatch (prompt changed or model bumped) is a hard error — re-record with `make eval-record`.

## Golden cases

| Case | Fixture | Type | Exercises |
|------|---------|------|-----------|
| ROLE-01 | `tests/fixtures.role_01()` | shortlist | All six invariants |
| ROLE-02 | `tests/fixtures.role_02()` | shortlist | gates-respected, evidence-cited |
| ROLE-03 | `tests/fixtures.role_03()` | no_match | gates-respected, no-PII-leak |
