# progress.B.md — Lane B: Reasoning

> Lane file. Owner: **Eng B — Reasoning** (clarify, score, rank, reasoning).
> Global facts (build phase, contracts, decisions) live in the index `docs/progress.md` — don't duplicate them here.
> Headers below are stable so `/handoff` can target them. Append to the session log, newest first; rewrite the other sections from current state.

## In flight
- _(none)_

## Next up
1. Real `score` reasoning — `score_candidate` DSPy module (skill match + feedback scoring, adjacency enforced in Python, AD-033).
2. Real `rank` — deterministic weighted combination + tie-break; `NoMatchResult` with near-misses when pool is empty.
3. Wire `dsm match` CLI to real clarify + score + rank end-to-end (currently clarify is real; score/rank still stub).

## Blockers / needs a human
- _(none)_

## Session log (append-only — newest first)
- **2026-06-17 · b-001-clarify** — Wrote spec `specs/b-001-clarify/` (requirements, design, tasks — 6 tasks, 13 ACs). Implemented B-001 through B-006: `ClarifyRole` DSPy signature, `PseudonymisedLM` wiring, deterministic fallback parser, `clarify_role` predict+parse+retry, retry/fallback tests, golden fixtures for ROLE-01/ROLE-02 (auto-discovered), `DSM_LIVE_LM=1` guard. `make check` GREEN (48 passed, 2 skipped, 2 import contracts). Branch: `spec/b-001-clarify`.
- **2026-06-14 · lane-split** — Split shared `progress.md` into per-lane files; seeded this Lane B file from the Reasoning slices. `make check` GREEN (29 tests, 2 import contracts). Next: real reasoning after Slice 1.
