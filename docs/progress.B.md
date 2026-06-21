# progress.B.md — Lane B: Reasoning

> Lane file. Owner: **Eng B — Reasoning** (clarify, score, rank, reasoning).
> Global facts (build phase, contracts, decisions) live in the index `docs/progress.md` — don't duplicate them here.
> Headers below are stable so `/handoff` can target them. Append to the session log, newest first; rewrite the other sections from current state.

## In flight
- _(none)_ — B-1 (query-time deterministic foundation) implemented on `feat/b/001-query-deterministic`; T-001…T-011 done, `make check` GREEN. Not yet merged to `main` (no PR opened).

## Next up
1. **B-2 — LLM-dependent scoring slice** (the explicit B-1 out-of-scope list): clarify (DSPy typed signature over `PseudonymisedLM`, falls back to deterministic echo), hybrid recall (dense + BM25 + RRF over the Milvus Lite `candidates` collection, `mode="query"` embed), rerank (cross-encoder), score + combine (`0.7·skill + 0.3·feedback`), and the candidate **hydration** path (`CandidateIndexRecord` → serving `Candidate` with `Skill.proficiency`/feedback — see `ee-query-architecture.md` §6.0/§12 #1).
2. **Orchestrator wiring** — thread the new B-1 modules into `dsm match`: `parse_demand` → `check_freshness` (refuse blocks the run) → existing gates → `exact_hard_skill_filter` → [B-2 recall/rerank/score] → `rank_assessments`. Today `dsm/index/stub.py` still backs `dsm match`.
3. **Open Roles CSV fixture** in `data/raw/demand/open_roles.csv` (intentionally deferred from B-1; `test_demand.py` builds CSVs in `tmp_path`).
4. **Distributed-gate detail wording** — the country-mismatch `LOCATION_MISMATCH` exclusion in `filter_candidates` still reuses the co-location-worded `detail` (dormant: all data is India). Tidy when the distributed gate gets real exercise.

## Blockers / needs a human
- **Merge B-1 to `main`** when ready — open the PR for `feat/b/001-query-deterministic`, then refresh the index via `/handoff-index` (AD-061). AD-086/088 are frozen-contract amendments that already touched Lane A's index records + Milvus schema and Lane C's gates/near-miss — coordinate the merge so other lanes re-pull the contract.

## Session log (append-only — newest first)
- **2026-06-21 · b-001-query-deterministic** — Implemented B-1 (T-001…T-011) on `feat/b/001-query-deterministic`, 7 commits. Ratified + applied **AD-086** (split `Location.remote_eligible` → `remote_within_country` + `onsite_cities`; onsite gate = city-match or onsite-city membership, `remote_within_country` never clears; distributed gate = same-country), **AD-087** (query-time freshness guard `check_freshness` → ok/warn/refuse), **AD-088** (`ExclusionReason.HARD_SKILL_MISMATCH`). The Location rename was done as **one atomic commit** across all consumers (models, silver `parse_location`, index `CandidateIndexRecord`/`FilterFields`/projections, Milvus schema `BOOL`+`ARRAY<VARCHAR>`, fixtures, near-miss wording, gates) to keep `make check` green — a frozen-field rename can't stay green per-task. New modules: `dsm/match/models.py` (`OpenRolesBanner`/`DemandParseOutcome`), `dsm/match/freshness.py`, `dsm/match/demand.py` (`parse_demand`: banner→`demand_as_of`, skill encodings, co-location, Notes→description, Priority order, log+skip malformed, missing-banner blocks), `dsm/index/retrieve.py` (`exact_hard_skill_filter`: set-membership + proficiency floor, no adjacency). `rank.py` verified unchanged vs §6.10 (docstring repointed). `make check` **GREEN** — 340 passed, 11 skipped, 3 import contracts. Corrected `.claude/lane` C→B (was mislabelled). Next: B-2 scoring + orchestrator wiring; merge B-1 to `main`.
- **2026-06-14 · lane-split** — Split shared `progress.md` into per-lane files; seeded this Lane B file from the Reasoning slices. `make check` GREEN (29 tests, 2 import contracts). Next: real reasoning after Slice 1.
