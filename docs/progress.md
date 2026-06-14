# progress.md — Live build state (index)

> **Read this first at the start of every session, then your lane file `docs/progress.<lane>.md`.** This is the shared index: global facts only (build phase, what works, active specs, decisions). Per-lane state lives in the lane files. (The rules of *how* we work live in `CLAUDE.md`.) A fresh session should be able to orient from this file alone.
> Per-lane progress goes in `docs/progress.A.md` / `.B.md` / `.C.md` — see _Lane files_ below. Section headers are stable so `/handoff-index` can target them. Keep them.

## Current status
- **Build phase:** Slice 0 complete; contracts frozen.
- **Active slice:** Slice 0 — Foundation (branch `spec/000-foundation`), ready to merge.
- **Harness (`make check`):** GREEN — format, lint, typecheck, 29 tests, import contracts all pass.
- **`main`:** clean; docs only. Spec branch ready to merge.

## Works end-to-end right now
- `uv run dsm match --role-id ROLE-STUB-01` — runs stub pipeline end-to-end, prints valid JSON.
- `dsm/models.py` — all 19 Pydantic v2 domain contracts typed and frozen (AD-060).
- `make check` — 29 tests green (27 model + 1 gates + 1 CLI e2e), 0 type errors, 2 import contracts.

## Lane files
Per-lane In flight / Next up / Blockers / Session log live in these append-only files. Read the index, then your lane file.
- [`docs/progress.A.md`](progress.A.md) — **Lane A: Data & Retrieval** (Eng A — ingest, index, gates, retrieval).
- [`docs/progress.B.md`](progress.B.md) — **Lane B: Reasoning** (Eng B — clarify, score, rank).
- [`docs/progress.C.md`](progress.C.md) — **Lane C: Quality, PII & Interface** (Eng C — PII boundary, CLI, eval/quality).

## Active specs
- `specs/000-foundation/` — complete and approved (branch `spec/000-foundation`).

## Decisions
- Authoritative log: `docs/decision.md` (current range AD-001 … AD-060). AD-060 added: domain contracts frozen.
- **Freeze the contracts after Slice 0.** Churn breaks parallel lane work — change only via team agreement + a new ADR.

---

## Session log — pre-split archive (frozen)
Shared history from before the per-lane split (2026-06-14). **Frozen — do not append here.** New entries go in the lane files' session logs.
- **2026-06-11 · slice-0-foundation** — Completed tasks F-005 through F-016: stub ingest/gates/clarify/score/rank/index/pii modules, CLI `dsm match`, `config/default.yaml`, eval scaffold, import-linter passing, 29 tests green. `make check` fully green. Slice 0 done. Next: merge to main, begin Slice 1 (real gates).
- **2026-06-11 · slice-0-models** — Implemented tasks F-003 + F-004: wrote `dsm/models.py` (19 Pydantic v2 frozen models, all enums as `StrEnum`) and `tests/test_models.py` (27 tests — instantiation, discriminated union, validation rejection). `make check` green. Added AD-060 (contracts frozen). Also fixed `pyproject.toml` import-linter config (`include_external_packages = true`). Next: F-005 stub ingest through F-016.
- **2026-06-11 · slice-0-spec** — Wrote and approved `specs/000-foundation/` (requirements, design, tasks). 16 tasks (F-001 to F-016) defined for frozen contracts + stubbed CLI + green harness. Committed to branch `spec/000-foundation`. No implementation yet. Next: execute tasks.
- **2026-06-08 · setup** — Created the operating-system layer (`CLAUDE`, `product`, `tech`, `structure`, `decision`) and this file. No code. Next: Slice 0 foundation.

---

## Maintaining this file
This index describes `main`. It is refreshed **only at merge to `main`**, by whoever merges, via `/handoff-index` — which rewrites the global sections (_Current status_, _Works end-to-end_, _Active specs_, _Decisions_). See `.claude/commands/handoff-index.md`. While working on a feature branch, do **not** edit this file — update only your own lane file via `/handoff` (lane resolved from `.claude/lane`; see `.claude/commands/handoff.md`).
