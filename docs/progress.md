# progress.md — Session handoff & live build state

> **Read this first at the start of every session. Update it last, before you stop.**
> Single source of truth for *where the build is right now* and *what to do next*. (The rules of *how* we work live in `CLAUDE.md`.) A fresh session should be able to resume from this file alone.
> Section headers below are stable so a `/handoff` command can target them (see foot of file). Keep them.

## Current status
- **Build phase:** Pre-implementation. Steering + decision layer complete; no code yet.
- **Active slice:** Slice 0 — Foundation (not started).
- **Harness (`make check`):** not created yet — it's the first task.
- **`main`:** clean; docs only.

## Works end-to-end right now
- Nothing yet. Repo contains the operating-system docs only (`CLAUDE.md`, `docs/*.md`).

## In flight (partially done — resume exactly here)
- _(none)_

## Next up (in order)
1. Write `specs/000-foundation/{requirements,design,tasks}.md`; **stop for human sign-off** before code.
2. Co-design and **freeze** `dsm/models.py` contracts: `Candidate`, `OpenRole`, `TargetProfileScorecard`, `EligiblePool`, `ExclusionLog`, `CandidateAssessment`, `ShortlistResult`.
3. Stand up the harness: `Makefile` (`make check` / `make eval`), `uv`, `mise`, `ruff`, `pyright`, `pytest`, `import-linter`, CI.
4. Build `match/gates.py` + unit tests (needs only the frozen contracts).
5. Ingest *sheets only* → `Candidate` (beach first); stub `clarify` + `score`; get a trivial end-to-end CLI run.

## Blockers / needs a human
- _(none)_

## Watch-outs / gotchas
- **Freeze the contracts after Day 1.** Churn here breaks parallel work — change only via team agreement + a new ADR in `docs/decision.md`.
- Ingest is the critical-path rock: keep it sheets-only in Slice 0, defer Docling enrichment to Slice 2.
- Lane ownership & per-slice plan: Data&Retrieval (Eng A) / Reasoning&PII (Eng B) / Decision,Interface&Quality (Eng C). _(See `docs/ownership.md` if created.)_

## Active specs
- `specs/000-foundation/` — to be written.

## Decisions
- Authoritative log: `docs/decision.md` (current range AD-001 … AD-052). New ADRs this session: _(none)_.

---

## Session log (append-only — newest first)
- **2026-06-08 · setup** — Created the operating-system layer (`CLAUDE`, `product`, `tech`, `structure`, `decision`) and this file. No code. Next: Slice 0 foundation.

---

## Maintaining this file
At session **start**: read top-to-bottom. At session **end**: refresh _Current status_, _Works end-to-end_, _In flight_, _Next up_, _Blockers_, _Watch-outs_, _Active specs_, _Decisions_; prepend one dated line to _Session log_.
Automate with a `/handoff` command (Claude Code: a markdown file at `.claude/commands/handoff.md`). It should: read `git log` since the last log entry and summarise changes; run `make check` and record green/red; rewrite the status sections from the current repo state; prepend a dated line to the session log; change no other file; then show the diff.
