# CLAUDE.md — Operating system for the Demand–Supply Matcher

You are an AI engineer working in this repository. **Every session: read `docs/progress.md` first (where the build is right now), then this file (the rules), then the relevant spec — not the whole repo.** This file is your constitution.

## What this is
A staffing decision engine: given one open role, return a **ranked, explainable shortlist** of consultants with the trade-offs surfaced for a human to decide. Structured RAG over a vector index. A 3-engineer / 15-day POC.
Current state → `docs/progress.md` · Product → `docs/product.md` · Tech → `docs/tech.md` · Layout & spec format → `docs/structure.md` · Decisions → `docs/decision.md`.

## Golden rules (never violate)
1. **Spec before code.** No implementation without an approved spec in `specs/<feature>/`. If none exists, write `requirements.md` → `design.md` → `tasks.md` and **stop for review**.
2. **Gates are deterministic.** Location + availability filtering is plain Python. An LLM must **never** decide eligibility.
3. **No bare LLM calls.** Every external LLM call goes through `pii/PseudonymisedLM`. No PII reaches OpenRouter unpseudonymised; no `name`/`email` reaches Modal. Ever.
4. **Typed everything.** Anything crossing a module boundary is a Pydantic model. No dicts-as-contracts.
5. **Don't re-litigate decisions.** Settled calls live in `docs/decision.md`. Read it before proposing alternatives. To change one, add a superseding ADR — don't silently diverge.
6. **Green harness or it isn't done.** See Definition of Done.

## Workflow — the spec-driven loop
1. **Orient** — read `docs/progress.md`, then the relevant steering docs + `docs/decision.md`.
2. **Spec** — write `specs/<feature>/requirements.md` → `design.md` → `tasks.md` (format in `docs/structure.md`). **Stop for human sign-off before code.**
3. **Implement** — one task at a time; **one task = one commit**, imperative and referencing the spec (e.g. `feat(gates): availability window per AD-021`).
4. **Verify** — `make check` green.
5. **Record** — append `docs/decision.md` for any real decision; update `docs/progress.md` for the next session.
6. **Refresh** — if reality diverged from a doc, fix it **in the same PR**. Stale docs are the main cause of drift.

## The harness — run these to verify your work
- `make check` — format, lint, typecheck, unit tests, import contracts. **Must be green before any commit.**
- `make check-all` — `make check` + `make eval`. Use once eval suite is configured.
- `uv run pytest` · `uv run pyright` · `uv run ruff check --fix && uv run ruff format`
- `make eval` — Promptfoo + DeepEval invariants: gates-respected · hard-skill-not-cleared-by-adjacency · evidence-cited · **no-PII-leak** · determinism. **Not yet configured — will fail until wired up.**

**Never disable a check to make it pass.** Fix the cause, or — if the check is wrong — change it in its own commit with a note.

## Definition of Done
Spec acceptance criteria met · `make check` green · new behaviour has a test · new decisions in `docs/decision.md` · `docs/progress.md` updated for the next session. Not before.

## Stop and ask the human when
- A spec is ambiguous or conflicts with `docs/decision.md`.
- A change would weaken the PII boundary or make a gate non-deterministic.
- You'd add a dependency not in `docs/tech.md`.
- An eval invariant would have to be relaxed to pass.
- Scope would exceed `docs/product.md` § Out of scope.

## Anti-drift habits
- Load only the relevant module + its typed contract, not the whole tree.
- One source of truth per fact — link, don't duplicate (rules in `product.md`/`tech.md`, history in `decision.md`, state in `progress.md`).
- Edit the real file; don't create `_v2`. Delete code; don't comment it out.
- End the session via `/handoff` so `docs/progress.md` is current — never end mid-task with a red harness without saying so there.
