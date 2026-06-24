# Requirements — b-003 Human-Readable Shortlist (identity de-anonymisation at the output edge)

> Slice B-3. Surfaces **real candidate identity** (name + email) in the final `dsm match` /
> `dsm explain` output, replacing the pseudonymised `candidate_id` that currently stands in for
> both. This is the **deferred "final human-facing identity rendering"** explicitly anticipated by
> **AD-091** ("real identity is substituted only at final human-facing rendering, which is
> deferred") and **AD-102** (the `FileVault` + `get_identity` read path that makes it possible).
>
> **Scope is presentation only — no pipeline, gate, scoring, recall, rerank, or ranking logic
> changes.** The query pipeline keeps producing pseudonymised results; a new de-anonymisation
> **render step at the CLI composition root** substitutes identity from the vault just before
> output. JSON structure is unchanged (decision: *JSON with real identity*, not a new formatted
> view). De-anonymisation applies to **all** identity-bearing output sections, including the full
> exclusion log (decision: *everything incl. exclusion log*).

## User story

As a staffer reading a shortlist (or a no-match) on the terminal, I want to see each candidate's
**actual name and email** — in the ranked shortlist, the near-misses, the closest-on-skills list,
and the exclusion log — instead of an opaque `candidate_id` hash, so I can act on the result
(contact the person, recognise who was excluded and why) without a separate lookup.

## Problem statement (current behaviour)

- The serving `Candidate.email` / `Candidate.name` are set to the pseudonymised `candidate_id`
  (AD-091, `dsm/cli/store.py` hydration) — never the raw values, which live only in the vault.
- Consequently every identity-bearing output field carries the `candidate_id`:
  - `ShortlistResult.ranked_assessments[].candidate.email` / `.name`
  - `NearMiss.candidate_email` / `.name` (both `near_misses` and `closest_on_skills`)
  - `Exclusion.candidate_email` in `exclusion_log`
- Everything else in the output is **already real data from gold** (location, skills,
  availability, feedback, profile_summary, scores, narrative, evidence) — only identity is masked.
- The real `(name, email)` already lives in the persistent `FileVault` (AD-102), and `_match_role`
  already constructs that vault and reads it for the query-time PII boundary
  ([`dsm/cli/commands.py`](../../dsm/cli/commands.py) `_pii_aware_score_predictor`). Nothing
  consumes it for output rendering yet.

## Why this is safe (the PII boundary is preserved)

The de-anonymisation happens **only at the CLI composition root**, as a post-step after
`run_match` returns. This holds the two load-bearing guarantees:

1. **`match ⊥ PII` import contract (AD-101):** `dsm.match` imports no `dsm.pii`. The render step
   lives in `dsm/cli/commands.py` (the only layer that already owns the vault), so the contract is
   untouched.
2. **`no-PII-leak` invariant requires no relaxation.** The invariant
   ([`dsm/eval/invariants.py`](../../dsm/eval/invariants.py) `no_pii_leak`) was written for this
   future: it governs **provider seam inputs** and **LLM narratives**, *not* the output identity
   fields ("Final human-facing output legitimately carries identity for the authorised reader;
   this invariant governs **provider** inputs, not output."). Real name/email in the output does
   not trip it.
3. **Determinism invariant is unaffected** because `run_match` / `_match_role` keep returning the
   pseudonymised result; de-anonymisation is a separate function applied after the pipeline.

## Acceptance criteria (EARS)

- **AC-1** — WHEN `dsm match` produces a `ShortlistResult` and the vault holds an identity for a
  ranked candidate's `candidate_id`, the system SHALL render that candidate's `email` and `name` as
  the **real** values from the vault in the printed JSON.
- **AC-2** — WHEN `dsm match` / `dsm explain` produces a `NoMatchResult`, the system SHALL render
  the real `candidate_email` and `name` for every `NearMiss` in **both** `near_misses` and
  `closest_on_skills`.
- **AC-3** — WHEN any result carries a non-empty `exclusion_log`, the system SHALL render the real
  `candidate_email` for every `Exclusion`.
- **AC-4** — WHEN the vault has **no** identity for a `candidate_id` (`get_identity` returns
  `None`), the system SHALL keep the pseudonymised `candidate_id` in that field and log a PII-safe
  `WARNING` (`candidate_id` only), proceeding without failing — consistent with **AD-103**
  (vault-miss is warn-only, never fail-closed).
- **AC-5** — The system SHALL perform de-anonymisation **only** in `dsm/cli/commands.py`;
  `dsm.match` SHALL NOT gain any `dsm.pii` import (the existing `match ⊥ PII` import-linter
  contract SHALL stay green).
- **AC-6** — `run_match` and `_match_role` SHALL continue to return **pseudonymised** results
  (identity = `candidate_id`); de-anonymisation SHALL be a separate render step applied at the
  `match` / `explain` command boundary before output. (Keeps the determinism invariant and the
  eval cassettes unchanged.)
- **AC-7** — The output **JSON structure** (field names, nesting) SHALL be unchanged; only the
  *values* of identity fields change. No new output format / view is added this slice.
- **AC-8** — No gate, scoring, recall, rerank, ranking, near-miss-selection, or exclusion logic
  SHALL change. `make check` SHALL stay green; `make eval` invariants SHALL pass **without any
  invariant being relaxed** (Golden Rule: stop if an invariant must be relaxed — it must not here).

## Out of scope

- A formatted/tabular human-readable terminal view (the decision was JSON with real identity).
- Any change to the query pipeline, gates, scoring, or ranking.
- At-rest vault encryption / retention / purge (**AD-068, still deferred**).
  - **Note (raised at spec review):** identity (name + email) is **not** newly stored by this
    slice — it already lives in `data/identity/vault.json` (the `FileVault`, AD-102), written by
    `dsm ingest` and gitignored. This slice only **reads** it for output rendering; it introduces
    no new store, table, or file. However, the vault is **plaintext today** (a signed-off POC
    limitation, AD-102), and surfacing real identities in `dsm match` / `dsm explain` output
    **increases visible exposure** of those values. This does not change the *at-rest* posture, but
    it makes the AD-068 hardening (encryption at rest, retention, purge-by-id) more pressing.
    AD-068 remains **out of scope here** and is the right place to address it — flagged so the
    trade-off is a conscious call, not silent drift.
- The generic outbound NER/org scan on the index/embed path (AD-084, still deferred).
