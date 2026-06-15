Refresh the shared index `docs/progress.md`. Run this **when merging a feature branch to `main`** — that's when shared state changes (a slice merged, the harness flipped, a spec moved, new ADRs landed). Run it as whoever is doing the merge; there's no separate role. For per-lane handoff on a feature branch use `/handoff` instead (lane resolved from `.claude/lane`) — this command never touches a lane file.

## 1. Read only the index
Read **only** `docs/progress.md`. **Do not read the lane files** — the index is refreshed from repo ground-truth, not by aggregating lane state. (That's what keeps the index thin and the lanes the single home for per-lane progress.)

## 2. Refresh each global section from its source of truth
Rewrite these four sections of `docs/progress.md` only — derive each from the stated source, not from memory or lane files:
- **Current status** ← `git` (branch / merge state) + `make check` result (record GREEN/RED) + the current slice.
- **Works end-to-end right now** ← the repo — what actually runs (e.g. `dsm match`, tests passing).
- **Active specs** ← the `specs/` directory (which are approved / in progress / done).
- **Decisions** ← `docs/decision.md` (update the AD range if new ADRs landed).

Leave the **Lane files**, **Session log — pre-split archive (frozen)**, and **Maintaining this file** sections untouched.

## 3. Touch no other file
Change **no file other than `docs/progress.md`** — never a lane file. Do not add a session-log entry (the archive is frozen; ongoing logs live in the lane files). Then show the diff of `docs/progress.md`.
