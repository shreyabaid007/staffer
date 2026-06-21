# progress.C.md — Lane C: Quality, PII & Interface

> Lane file. Owner: **Eng C — Quality, PII & Interface** (PII boundary, CLI/interface, eval/quality).
> Global facts (build phase, contracts, decisions) live in the index `docs/progress.md` — don't duplicate them here.
> Headers below are stable so `/handoff` can target them. Append to the session log, newest first; rewrite the other sections from current state.

## In flight
- _(none)_

## Next up
1. **Query-time pipeline architecture** — design the query-time pipeline (companion to `ee-ingestion-architecture.md`). This will settle gating, retrieval, scoring, and ranking design. The c-001 gates+rank implementation is **deprecated (AD-085)** and will be revisited/replaced.
2. **Real `pii/PseudonymisedLM` boundary** (currently stubbed) — Presidio/spaCy anonymise-before / deanonymise-after so no PII reaches OpenRouter unpseudonymised and no `name`/`email` reaches Modal. Needs a spec before code (golden rule 1).
3. Wire up `make eval` — Promptfoo + DeepEval invariants (evidence-cited · no-PII-leak · determinism). Eval cases and invariants will be redesigned alongside the query architecture.

## Blockers / needs a human
- _(none)_

## Session log (append-only — newest first)
- **2026-06-16 · c-001-gates-rank** — Implemented T-001…T-007: real location + availability gates (`dsm/match/gates.py`; AD-020/021/022, AD-063a; shared `effective_free_date`), config-free rank sort/tie-break/top-k (`dsm/match/rank.py`; AD-043), orchestrator no-match path + `build_near_misses` (`dsm/cli/commands.py`; AD-063b/c/d, gaps recomputed from structured data not `detail`), and importable ROLE-01/02/03 fixtures (`tests/fixtures/`). Added `dsm/config.py` YAML loader + declared PyYAML, recorded as **AD-064** (orchestrator owns config + `config_snapshot`; rank stays config-free) — confirmed with the human at the post-T-003 checkpoint before adding the dep. All 24 EARS criteria covered by tests; adversarial review clean; closed the design's empty-candidate-list edge case. `make check` GREEN (66 tests, 2 import contracts; `gates.py` import-clean). Next: merge + `/handoff-index`, then wire `make eval` on the seed fixtures.
- **2026-06-14 · lane-split** — Split shared `progress.md` into per-lane files; seeded this Lane C file from the Quality/PII/Interface slices. `make check` GREEN (29 tests, 2 import contracts). Next: wire up `make eval`.
