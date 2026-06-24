# Design — b-003 Human-Readable Shortlist

## Proposed decision

**AD-107 · Final human-facing identity de-anonymisation at the CLI output edge.** The query
pipeline (`dsm.match`) keeps emitting pseudonymised results (identity field = `candidate_id`,
AD-091). A new render step `render_identities(result, vault)` in `dsm/cli/commands.py` (the
composition root that already owns the `FileVault`) substitutes real `(name, email)` from the
vault into every identity-bearing output field, applied by the `match` / `explain` commands
**after** `run_match` returns and **before** serialisation. Vault-miss → keep `candidate_id` +
PII-safe warn (AD-103). This is the deferred half of AD-091/AD-102; it touches no frozen model
(no field added — only values change) and requires no eval-invariant relaxation (the `no-PII-leak`
invariant governs provider inputs/narratives, not output identity, by design). To be recorded in
`docs/decision.md` at implementation.

**Storage note (no new store; AD-068 visibility).** This slice adds **no** identity storage. Names
and emails already live in `data/identity/vault.json` (the `FileVault`, AD-102), written by
`dsm ingest` and gitignored; gold holds only `*_vault_ref` pointers. `render_identities` is simply
a **second reader** of that file (the first being `_pii_aware_score_predictor`), reusing the same
vault instance. The at-rest posture is unchanged — but the vault is **plaintext today** (signed-off
POC limitation, AD-102), and printing real identities in output raises their *visible* exposure.
At-rest encryption / retention / purge-by-id stays **out of scope** and remains AD-068's to land;
recorded here so the heightened exposure is a conscious trade-off, not silent drift.

## Modules touched

- **`dsm/cli/commands.py`** — the only file with code changes:
  - New pure-ish helper `render_identities(result: ShortlistResult | NoMatchResult, vault: Vault)
    -> ShortlistResult | NoMatchResult`. Reads identity via `vault.get_identity(candidate_id)`;
    rebuilds frozen models via `model_copy(update=...)`.
  - `_match_role` returns the vault alongside the result (or the commands rebuild the same vault),
    so `match` / `explain` can pass it to `render_identities`. Preferred: change `_match_role`'s
    return to `tuple[ShortlistResult | NoMatchResult, Vault]` (internal helper, not a public
    contract) so the same vault instance is reused and no second `FileVault` is constructed.
  - `match`: `result, vault = _match_role(...)` → `result = render_identities(result, vault)` →
    `typer.echo(result.model_dump_json(indent=2))`.
  - `explain`: same render before `_lineage(result)`. `_lineage` and `_exclusion_lines` are left
    **unchanged** — they read `candidate_email` / `.email` off whatever result they're handed, so
    rendering upstream makes the lineage human-readable for free.
- **No change** to `dsm/match/*`, `dsm/index/*`, `dsm/models.py`, `dsm/pii/*`, or `config/`.

## Data contracts

No new or changed Pydantic models (AC-7). All output models are `frozen=True`, so de-anonymisation
produces **copies** via `model_copy(update=...)`:

- `Candidate.model_copy(update={"email": real_email, "name": real_name})`
- `CandidateAssessment.model_copy(update={"candidate": <rebuilt candidate>})`
- `NearMiss.model_copy(update={"candidate_email": real_email, "name": real_name})`
- `Exclusion.model_copy(update={"candidate_email": real_email})`
- `ShortlistResult` / `NoMatchResult` / `ExclusionLog` rebuilt with their updated child lists.

### Vault key + identity order

- The lookup key is the `candidate_id`, which is carried in `Candidate.email` /
  `NearMiss.candidate_email` / `Exclusion.candidate_email` (AD-091).
- `Vault.get_identity(candidate_id) -> tuple[str, str] | None` returns `(name, email)` — matching
  the ingest write `vault.put_identity(cid, name, email)` (AD-102). The render step destructures
  `name, email = identity`.

## Phases involved

Output / serving edge only (post-rank, pre-print). No ingestion, index, gate, clarify, score, or
rank phase logic is involved.

## Edge cases

- **Vault miss (`get_identity` → None):** keep the `candidate_id` in the field, emit
  `_log.warning("render.vault_miss_identity", candidate_id=<cid>)` (PII-safe — `candidate_id`
  only), continue. Mirrors AD-103's warn-only posture. (Distinct log key from
  `score.vault_miss_reduced_redaction` so the two paths are separable in logs.)
- **Empty exclusion log / empty near-miss lists:** render is a no-op over empty lists.
- **`NoMatchResult` with no near-misses but a populated exclusion log:** AC-3 still applies — every
  `Exclusion` is de-anonymised.
- **Duplicate `candidate_id` across sections** (e.g. same person in a near-miss and an exclusion):
  each lookup is independent and idempotent; a tiny per-call cache (`dict[str, tuple|None]`) avoids
  repeat vault reads but is optional.
- **De-anon must not touch narratives/evidence/scores:** `render_identities` only rewrites the
  named identity fields; it never inspects `narrative`, `evidence`, or score fields (those are
  already governed by the PII boundary at score time).

## Eval cases to add

All under `make check` (deterministic, no network) — these are unit/CLI tests, not new eval-suite
invariants (the existing `no-PII-leak` invariant already covers the boundary and is **not**
modified):

1. **`tests/cli/test_render_identities.py`** (new):
   - `ShortlistResult`: ranked candidate's `email`/`name` become the vault's real values (AC-1).
   - `NoMatchResult`: every `near_misses` and `closest_on_skills` entry de-anonymised (AC-2).
   - Exclusion log: every `Exclusion.candidate_email` de-anonymised (AC-3).
   - Vault-miss: `candidate_id` retained, warning logged, no exception (AC-4).
   - Idempotence/purity: input result is unchanged (frozen copy semantics); rendering twice yields
     equal output.
2. **CLI smoke** (extend existing `match`/`explain` CLI test or add one): with a seeded
   `FileVault`, the printed JSON contains the real email and not the `candidate_id` for the top
   candidate; with an empty vault, it falls back to the `candidate_id` and still prints valid JSON.
3. **Import contract:** the existing `match ⊥ PII` contract continues to pass (AC-5) — verified by
   `make check` (no new test needed; rendering lives in `dsm/cli`).

## What is explicitly NOT changed (regression guard)

- `run_match` / `_match_role` pipeline output stays pseudonymised (AC-6) — the determinism
  invariant and the eval cassettes (`tests/eval/`) keep passing untouched.
- No frozen model amendment (contrast b-002/b-004 which added fields) — so other lanes do **not**
  need to re-pull `dsm/models.py`.
