# progress.md — Session handoff & live build state

> **Read this first at the start of every session. Update it last, before you stop.**
> Single source of truth for *where the build is right now* and *what to do next*. (The rules of *how* we work live in `CLAUDE.md`.) A fresh session should be able to resume from this file alone.
> Section headers below are stable so a `/handoff` command can target them (see foot of file). Keep them.

## Current status
- **Build phase:** Slice 0 complete; contracts frozen.
- **Active slice:** Slice 0 — Foundation (branch `spec/000-foundation`), ready to merge.
- **Harness (`make check`):** GREEN — format, lint, typecheck, 29 tests, import contracts all pass.
- **`main`:** clean; docs only. Spec branch ready to merge.

## Works end-to-end right now
- `uv run dsm match --role-id ROLE-STUB-01` — runs stub pipeline end-to-end, prints valid JSON.
- `dsm/models.py` — all 19 Pydantic v2 domain contracts typed and frozen (AD-060).
- `make check` — 29 tests green (27 model + 1 gates + 1 CLI e2e), 0 type errors, 2 import contracts.

## In flight (partially done — resume exactly here)
- _(none — Slice 0 fully complete)_

## Next up (in order)
1. Merge `spec/000-foundation` → `main`.
2. Begin Slice 1 — implement real gates.py (location + availability filtering).

## Blockers / needs a human
- _(none)_

## Watch-outs / gotchas
- **Freeze the contracts after Slice 0 completes.** Churn here breaks parallel work — change only via team agreement + a new ADR in `docs/decision.md`.
- Ingest is the critical-path rock: keep it sheets-only in Slice 0, defer Docling enrichment to Slice 2.
- Lane ownership & per-slice plan: Data&Retrieval (Eng A) / Reasoning&PII (Eng B) / Decision,Interface&Quality (Eng C). _(See `docs/ownership.md` if created.)_

## Active specs
- `specs/000-foundation/` — complete and approved (branch `spec/000-foundation`).

## Decisions
- Authoritative log: `docs/decision.md` (current range AD-001 … AD-060). AD-060 added: domain contracts frozen.

---

## Session log (append-only — newest first)
- **2026-06-11 · slice-0-foundation** — Completed tasks F-005 through F-016: stub ingest/gates/clarify/score/rank/index/pii modules, CLI `dsm match`, `config/default.yaml`, eval scaffold, import-linter passing, 29 tests green. `make check` fully green. Slice 0 done. Next: merge to main, begin Slice 1 (real gates).
- **2026-06-11 · slice-0-models** — Implemented tasks F-003 + F-004: wrote `dsm/models.py` (19 Pydantic v2 frozen models, all enums as `StrEnum`) and `tests/test_models.py` (27 tests — instantiation, discriminated union, validation rejection). `make check` green. Added AD-060 (contracts frozen). Also fixed `pyproject.toml` import-linter config (`include_external_packages = true`). Next: F-005 stub ingest through F-016.
- **2026-06-11 · slice-0-spec** — Wrote and approved `specs/000-foundation/` (requirements, design, tasks). 16 tasks (F-001 to F-016) defined for frozen contracts + stubbed CLI + green harness. Committed to branch `spec/000-foundation`. No implementation yet. Next: execute tasks.
- **2026-06-08 · setup** — Created the operating-system layer (`CLAUDE`, `product`, `tech`, `structure`, `decision`) and this file. No code. Next: Slice 0 foundation.

---

## Maintaining this file
At session **start**: read top-to-bottom. At session **end**: refresh _Current status_, _Works end-to-end_, _In flight_, _Next up_, _Blockers_, _Watch-outs_, _Active specs_, _Decisions_; prepend one dated line to _Session log_.
Automate with a `/handoff` command (Claude Code: a markdown file at `.claude/commands/handoff.md`). It should: read `git log` since the last log entry and summarise changes; run `make check` and record green/red; rewrite the status sections from the current repo state; prepend a dated line to the session log; change no other file; then show the diff.
