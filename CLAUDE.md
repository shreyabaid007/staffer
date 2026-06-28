# CLAUDE.md — Operating system for the Demand–Supply Matcher

You are an AI engineer working in this repository. **Every session: read `docs/progress.md` (index) first, then your lane file `docs/progress.<lane>.md` (where your lane's build is right now), then this file (the rules), then the relevant spec — not the whole repo.** This file is your constitution.

## What this is
A staffing decision engine: given one open role, return a **ranked, explainable shortlist** of consultants with the trade-offs surfaced for a human to decide. Structured RAG over a vector index. A 3-engineer / 15-day POC.
Current state → `docs/progress.md` (index) + `docs/progress.{A,B,C}.md` (per-lane) · Product → `docs/product.md` · Tech → `docs/tech.md` · Layout & spec format → `docs/structure.md` · Decisions → `docs/decision.md`.

## Golden rules (never violate)
1. **Spec before code.** No implementation without an approved spec in `specs/<feature>/`. If none exists, write `requirements.md` → `design.md` → `tasks.md` and **stop for review**.
2. **Gates are deterministic.** Location + availability filtering is plain Python. An LLM must **never** decide eligibility.
3. **No bare LLM calls.** Every external LLM call goes through `pii/PseudonymisedLM`. No PII reaches OpenRouter unpseudonymised; no `name`/`email` reaches Modal. Ever.
4. **Typed everything.** Anything crossing a module boundary is a Pydantic model. No dicts-as-contracts.
5. **Don't re-litigate decisions.** Settled calls live in `docs/decision.md`. Read it before proposing alternatives. To change one, add a superseding ADR — don't silently diverge.
6. **Green harness or it isn't done.** See Definition of Done.

## Workflow — the spec-driven loop
1. **Orient** — read `docs/progress.md` (index) + your lane file `docs/progress.<lane>.md`, then the relevant steering docs + `docs/decision.md`.
2. **Spec** — write `specs/<feature>/requirements.md` → `design.md` → `tasks.md` (format in `docs/structure.md`). **Stop for human sign-off before code.**
3. **Implement** — one task at a time; **one task = one commit**, imperative and referencing the spec (e.g. `feat(gates): availability window per AD-021`).
4. **Verify** — `make check` green.
5. **Record** — append `docs/decision.md` for any real decision; update your lane file `docs/progress.<lane>.md` via `/handoff` (lane resolved from `.claude/lane`) for the next session. The shared index `docs/progress.md` describes `main` — refresh it only when merging to `main`, via `/handoff-index` (AD-061).
6. **Refresh** — if reality diverged from a doc, fix it **in the same PR**. Stale docs are the main cause of drift.

## The harness — run these to verify your work
- `make check` — format, lint, typecheck, unit tests, import contracts, **Tier-1 eval invariants**. **Must be green before any commit.**
- `make check-all` — `make check` + `make eval`.
- `uv run pytest` · `uv run pyright` · `uv run ruff check --fix && uv run ruff format`
- `make eval` — the three-tier pytest eval harness (AD-095/096): **Tier 1** deterministic invariants (gates-respected · hard-skill-not-cleared-by-adjacency · evidence-cited · **no-PII-leak** · determinism · adjacency-flag), **Tier 2** signature/cassette regression, **Tier 3** live smoke + drift guard, plus the AI eval layer — DeepEval faithfulness judge + retrieval metrics (AD-104/105/106). **Configured and green**; the live tiers `skipif` no API keys. (No Promptfoo — dropped in AD-095.)

**Never disable a check to make it pass.** Fix the cause, or — if the check is wrong — change it in its own commit with a note.

## Definition of Done
Spec acceptance criteria met · `make check` green · new behaviour has a test · new decisions in `docs/decision.md` · your lane file `docs/progress.<lane>.md` updated for the next session. Not before.

## Stop and ask the human when
- A spec is ambiguous or conflicts with `docs/decision.md`.
- A change would weaken the PII boundary or make a gate non-deterministic.
- You'd add a dependency not in `docs/tech.md`.
- An eval invariant would have to be relaxed to pass.
- Scope would exceed `docs/product.md` § Out of scope.

## Anti-drift habits
- Load only the relevant module + its typed contract, not the whole tree.
- One source of truth per fact — link, don't duplicate (rules in `product.md`/`tech.md`, history in `decision.md`, global state in `progress.md`, per-lane state in `progress.<lane>.md`).
- Edit the real file; don't create `_v2`. Delete code; don't comment it out.
- End the session via `/handoff` (lane from `.claude/lane`) so your lane file `docs/progress.<lane>.md` is current — never end mid-task with a red harness without saying so there.

## Doc hygiene (enforced by `tests/docs` in `make check`)
- **Never restate a config value or a count in living prose** — cite the key instead (e.g. "`config/default.yaml::index.recall.enabled`") rather than writing out its on/off value. Config/code is the only source of truth for a value; restating it creates a drift site (this is what rotted that default across ~6 files). `tests/docs/test_doc_invariants.py` fails the build when a steering doc restates a volatile value that contradicts config.
- **Counts (tests / import-contracts / PR numbers) belong only in append-only session logs** (point-in-time history), never in a living section — they are wrong within a week.
- **ADRs:** one definition per id; the `decision.md` footer's "next AD-NNN" and `progress.md`'s "current range" must track the log (enforced). On a feature branch use an **`AD-XXX` placeholder** and let `/handoff-index` assign the real number at merge — this avoids cross-lane id collisions. Change a decision only by a **superseding entry / inline `superseded by AD-N` note**, never by silent edit.
- **Design docs** (`ee-*-architecture.md`) carry a `Status:` header; once a design is implemented, **code + ADRs are the truth** and the design doc must say so (add an "amendment since sign-off" note rather than letting it read as authoritative-but-wrong).
- If you add a new always-true cross-doc fact (an ADR id, a config key, a module path in a steering doc), make sure it survives `make docs-check`; add an assertion if it's worth guarding.
- **Orienting fast:** `make decisions-status` prints the decisions currently **in force** (derived from `decision.md` — no need to replay every ADR). `docs/backlog.md` is the consolidated deferred-work / known-debt list. `dsm/models.py` (AD-060) is snapshotted — change it only with an ADR + `make contract-snapshot`.
