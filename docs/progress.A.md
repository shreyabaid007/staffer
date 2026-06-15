# progress.A.md — Lane A: Data & Retrieval

> Lane file. Owner: **Eng A — Data & Retrieval** (ingest, index, gates, retrieval).
> Global facts (build phase, contracts, decisions) live in the index `docs/progress.md` — don't duplicate them here.
> Headers below are stable so `/handoff` can target them. Append to the session log, newest first; rewrite the other sections from current state.

## In flight
- _(none)_

## Next up
1. Slice 1 — implement real `gates.py` (deterministic location + availability filtering). _(Gates are plain Python — an LLM must never decide eligibility.)_
2. Real ingest — keep it **sheets-only** in Slice 1; defer Docling enrichment to Slice 2. _(Ingest is the critical-path rock.)_
3. Real index/retrieval over the vector store.

## Blockers / needs a human
- _(none)_

## Session log (append-only — newest first)
- **2026-06-14 · lane-split** — Split shared `progress.md` into per-lane files; seeded this Lane A file from the Data & Retrieval slices. `make check` GREEN (29 tests, 2 import contracts). Next: Slice 1 real gates.
