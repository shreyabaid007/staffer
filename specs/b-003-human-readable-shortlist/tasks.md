# Tasks — b-003 Human-Readable Shortlist

> Ordered, atomic, one task = one commit, each mapped to an acceptance criterion in
> `requirements.md`. Branch: `feat/b/003-human-readable-shortlist`. **No code until this spec is
> signed off (Golden Rule 1).**

- **T-000 · Branch + record AD-107.**
  Create `feat/b/003-human-readable-shortlist`. Append **AD-107** (final human-facing identity
  de-anonymisation at the CLI output edge) to `docs/decision.md`. No code yet.
  → covers the decision-record half of AC-5/AC-6.

- **T-001 · `render_identities` helper (deterministic, vault-backed).**
  Add `render_identities(result, vault) -> ShortlistResult | NoMatchResult` to
  `dsm/cli/commands.py`: rebuild identity fields on `ranked_assessments[].candidate`, both
  `NearMiss` lists, and every `Exclusion`, via `model_copy(update=...)` using
  `vault.get_identity(candidate_id)`. Vault-miss → keep `candidate_id` + PII-safe
  `WARNING` (`render.vault_miss_identity`, `candidate_id` only). Pure w.r.t. its input (returns
  copies; never mutates).
  → AC-1, AC-2, AC-3, AC-4, AC-7.

- **T-002 · Wire the render step into `match` and `explain`.**
  Change `_match_role` to return `(result, vault)` (internal helper signature only). In `match`,
  call `render_identities` before `model_dump_json`; in `explain`, call it before `_lineage`.
  `_lineage` / `_exclusion_lines` unchanged. Confirm `dsm.match` gains no `dsm.pii` import.
  → AC-5, AC-6.

- **T-003 · Unit tests for `render_identities`.**
  Add `tests/cli/test_render_identities.py`: shortlist de-anon (AC-1), no-match near-miss +
  closest-on-skills de-anon (AC-2), exclusion-log de-anon (AC-3), vault-miss fallback + warning
  (AC-4), idempotence/input-unchanged. Use a fake/seeded vault (no network).
  → AC-1, AC-2, AC-3, AC-4.

- **T-004 · CLI smoke + green harness.**
  Extend the `match`/`explain` CLI test: seeded `FileVault` → printed JSON shows real email (not
  `candidate_id`) for the top candidate; empty vault → `candidate_id` fallback, still valid JSON.
  Run `make check` (must be green, incl. the `match ⊥ PII` contract) and `make eval` (invariants
  pass **with no relaxation**).
  → AC-5, AC-8.

- **T-005 · Docs refresh (same PR).**
  Update the Lane-B file `docs/progress.B.md` (session log + Next-up), and note in the b-002/b-004
  "deferred: final human-facing identity rendering" lines that AD-107 closes them. Refresh
  `docs/progress.md` only at merge to `main` via `/handoff-index` (AD-061).
  → Definition of Done.

## Definition of Done

Spec acceptance criteria (AC-1…AC-8) met · `make check` green · `make eval` invariants pass with
no invariant relaxed · new behaviour covered by `tests/cli/test_render_identities.py` + CLI smoke ·
AD-107 in `docs/decision.md` · `docs/progress.B.md` updated for the next session.
