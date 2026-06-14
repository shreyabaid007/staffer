# progress.C.md — Lane C: Quality, PII & Interface

> Lane file. Owner: **Eng C — Quality, PII & Interface** (PII boundary, CLI/interface, eval/quality).
> Global facts (build phase, contracts, decisions) live in the index `docs/progress.md` — don't duplicate them here.
> Headers below are stable so `/handoff` can target them. Append to the session log, newest first; rewrite the other sections from current state.

## In flight
- _(none)_

## Next up
1. Wire up `make eval` — Promptfoo + DeepEval invariants (gates-respected · hard-skill-not-cleared-by-adjacency · evidence-cited · no-PII-leak · determinism). Currently not configured and will fail until wired up.
2. Real `pii/PseudonymisedLM` boundary (currently stubbed) — no PII to OpenRouter unpseudonymised; no `name`/`email` to Modal.
3. Flesh out the `dsm match` CLI/interface beyond the stub pipeline.

## Blockers / needs a human
- _(none)_

## Session log (append-only — newest first)
- **2026-06-14 · lane-split** — Split shared `progress.md` into per-lane files; seeded this Lane C file from the Quality/PII/Interface slices. `make check` GREEN (29 tests, 2 import contracts). Next: wire up `make eval`.
