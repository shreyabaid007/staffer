# progress.md — Live build state (index)

> **Read this first at the start of every session, then your lane file `docs/progress.<lane>.md`.** This is the shared index: global facts only (build phase, what works, active specs, decisions). Per-lane state lives in the lane files. (The rules of *how* we work live in `CLAUDE.md`.) A fresh session should be able to orient from this file alone.
> Per-lane progress goes in `docs/progress.A.md` / `.B.md` / `.C.md` — see _Lane files_ below. Section headers are stable so `/handoff-index` can target them. Keep them.

## Current status
- **Build phase:** Slice 1 underway — real deterministic gates + rank merged; LLM/PII/retrieval steps still stubbed. Contracts frozen.
- **Active slice:** Lane C `c-001-gates-rank` merged to `main` (PR #5). Lane A `feat/a/001-ingest-sheets` in flight (real xlsx ingest); Lane B reasoning not yet started.
- **Harness (`make check`):** GREEN — format, lint, typecheck, 66 tests, 2 import contracts all pass.
- **`main`:** Slice 0 foundation (PR #3) + per-lane progress files (PR #4); index refresh (PR #6); real gates/rank/no-match (PR #5, `feat/c/001-gates-rank`).

## Works end-to-end right now
- `uv run dsm match --role-id ROLE-STUB-01` — runs the full pipeline (real gates → stub retrieve/score → real rank, or real no-match) over stub ingest and prints a valid `ShortlistResult` / `NoMatchResult` JSON.
- **Real deterministic gates** ([`dsm/match/gates.py`](../dsm/match/gates.py)) — location (AD-020/063a) + availability (AD-021/022); LLM-free, import-clean.
- **Real ranking** ([`dsm/match/rank.py`](../dsm/match/rank.py)) — deterministic sort/tie-break/top-k; config-free (orchestrator owns config via [`dsm/config.py`](../dsm/config.py), AD-064).
- **No-match path + near-misses** ([`dsm/cli/commands.py`](../dsm/cli/commands.py)) — orchestrator builds `NoMatchResult` with ordered, capped near-misses (AD-063b/c/d).
- `dsm/models.py` — all 19 Pydantic v2 domain contracts typed and frozen (AD-060).
- `make check` — 66 tests green (gates, rank, no-match, e2e + fixtures, models), 0 type errors, 2 import contracts. Importable ROLE-01/02/03 seed fixtures in `tests/fixtures/` (reusable by `dsm/eval/`).

## Lane files
Per-lane In flight / Next up / Blockers / Session log live in these append-only files. Read the index, then your lane file.
- [`docs/progress.A.md`](progress.A.md) — **Lane A: Data & Retrieval** (Eng A — ingest, index, gates, retrieval).
- [`docs/progress.B.md`](progress.B.md) — **Lane B: Reasoning** (Eng B — clarify, score, rank).
- [`docs/progress.C.md`](progress.C.md) — **Lane C: Quality, PII & Interface** (Eng C — PII boundary, CLI, eval/quality).

## Active specs
- `specs/000-foundation/` — complete, approved, merged to `main`.
- `specs/c-001-gates-rank/` — complete, approved, merged to `main` (PR #5).
- _Next (planned):_ Lane C `c-002-*` for the real `pii/PseudonymisedLM` boundary (not yet written).

## Decisions
- Authoritative log: `docs/decision.md` (current range AD-001 … AD-064; next starts at AD-065). Recently landed: AD-062 (revised lane assignments), AD-063 (gate semantics + near-miss assembly), AD-064 (YAML config loader + PyYAML dep; rank stays config-free).
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
