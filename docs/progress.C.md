# progress.C.md — Lane C: Quality, PII & Interface

> Lane file. Owner: **Eng C — Quality, PII & Interface** (PII boundary, CLI/interface, eval/quality).
> Global facts (build phase, contracts, decisions) live in the index `docs/progress.md` — don't duplicate them here.
> Headers below are stable so `/handoff` can target them. Append to the session log, newest first; rewrite the other sections from current state.

## In flight
- _(none)_

## Next up
1. **Real `pii/PseudonymisedLM` boundary** (currently stubbed) — Presidio/spaCy anonymise-before / deanonymise-after so no PII reaches OpenRouter unpseudonymised and no `name`/`email` reaches Modal. Needs a spec before code (golden rule 1).
2. **Narrative-faithfulness LLM judge** — G-Eval judge for narrative quality. Deferred from c-002: needs labelled validation data + TPR/TNR ≥ 80% validation before wiring.
3. **Retrieval-quality metrics** (Recall@K, contextual precision/recall) — meaningful only once hybrid recall flips ON (AD-089).

## Blockers / needs a human
- _(none)_

## Session log (append-only — newest first)
- **2026-06-23 · c-002-query-eval-harness** — Implemented T-000 through T-010: ratified AD-093 (three-tier eval architecture, drop promptfoo) + AD-094 (marker scheme + collection rules). Built six pure invariant evaluators in `dsm/eval/invariants.py` (gates-respected, hard-skill-not-cleared-by-adjacency, evidence-cited, no-PII-leak, determinism, adjacency-flag). Created golden-case + cassette-LM framework in `dsm/eval/cases.py` with `pseudonymise_candidates` mirroring `GoldCandidateStore`. Hand-authored cassette JSON fixtures for ROLE-01/02/03 under `tests/fixtures/cassettes/`. Enriched seed fixtures with `profile_summary` and `FeedbackEntry` items; added Suresh (java, HARD_SKILL_MISMATCH) + `desired_skills=[java/DESIRED]` to ROLE-01 for adjacency testing. Tier-1 runner: 17 tests (all six invariants × golden cases + deliberately-failing tampered fixtures + provider guard proof). Tier-2 signature regression: 7 tests (cassette freshness + clarify/score shape pins). Tier-3 live smoke + drift guard: 2 tests (skipif no keys). Wired `make eval` (no longer stubs), `make eval-tier1` (Tier-1 in `make check`), `make eval-record` (cassette regeneration). Updated eval README. `make check` GREEN (387 unit + 17 Tier-1 eval, 4 contracts). `make eval` GREEN (26 tests). Branch: `feat/c/002-query-eval-harness`. Next: merge to main, then real `PseudonymisedLM` boundary.
- **2026-06-16 · c-001-gates-rank** — Implemented T-001…T-007: real location + availability gates (`dsm/match/gates.py`; AD-020/021/022, AD-063a; shared `effective_free_date`), config-free rank sort/tie-break/top-k (`dsm/match/rank.py`; AD-043), orchestrator no-match path + `build_near_misses` (`dsm/cli/commands.py`; AD-063b/c/d, gaps recomputed from structured data not `detail`), and importable ROLE-01/02/03 fixtures (`tests/fixtures/`). Added `dsm/config.py` YAML loader + declared PyYAML, recorded as **AD-064** (orchestrator owns config + `config_snapshot`; rank stays config-free) — confirmed with the human at the post-T-003 checkpoint before adding the dep. All 24 EARS criteria covered by tests; adversarial review clean; closed the design's empty-candidate-list edge case. `make check` GREEN (66 tests, 2 import contracts; `gates.py` import-clean). Next: merge + `/handoff-index`, then wire `make eval` on the seed fixtures.
- **2026-06-14 · lane-split** — Split shared `progress.md` into per-lane files; seeded this Lane C file from the Quality/PII/Interface slices. `make check` GREEN (29 tests, 2 import contracts). Next: wire up `make eval`.
