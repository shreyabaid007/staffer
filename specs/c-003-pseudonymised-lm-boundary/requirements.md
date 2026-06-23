# Requirements — c-003 Real PseudonymisedLM Boundary

> EARS-form acceptance criteria. Each references a product invariant (`product.md` §Product
> invariants), the PII golden rule (`CLAUDE.md` golden rule 3), and the relevant ADR
> (AD-010/068/069 + the new AD-101/098). Machine-verifiable where possible.
>
> **Slice scope (confirmed with human):** make `pii/PseudonymisedLM` a *real* anonymise →
> leak-scan → call → de-anonymise boundary, backed by a *minimal persistent vault* read at
> query time. **Out of scope:** de-pseudonymising names in the final shortlist output;
> encryption / retention / purge hardening of the vault (AD-068, a later slice); the generic
> outbound NER / client-org-dictionary scan on the index path (AD-084 seam).

---

## R-01 · PseudonymisedLM anonymises every outbound call

**WHEN** `PseudonymisedLM.__call__` is invoked with a `prompt` and/or `messages` while a
non-empty known-PII call-context is active, the system **SHALL** redact every outbound text
fragment (the `prompt` string and each message `content`) via `dsm.pii.redact.redact`
(deterministic known-identifier strip first, then NER residual) **before** forwarding to the
wrapped provider, using a single coherent placeholder mapping for the whole call.

**WHEN** the wrapped provider returns a response, the system **SHALL** de-anonymise it
(`dsm.pii.redact.deanonymize`) using that same call mapping, so placeholders the model echoed
(e.g. verbatim citation quotes) are restored to their originals before the response leaves the
wrapper.

## R-02 · Outbound leak-scan is a hard gate

**WHEN** redaction has run and any *known* PII string still survives in a text fragment bound
for the provider, the system **SHALL** call `dsm.pii.leakscan.assert_no_leak` and **raise
`PIILeakError`**, blocking the provider call — it **SHALL NOT** forward the text. The error
message reports a count, never the PII value (`tech.md` "never log the pseudonym map").

## R-03 · Unset context is an explicit pass-through (clarify)

**WHEN** `PseudonymisedLM.__call__` runs with **no** known-PII call-context set (the default —
e.g. the `clarify` stage, which sees role text only and carries no candidate PII per
`ee-query-architecture.md` §7), the system **SHALL** forward the call unchanged (no redaction,
no leak-scan), preserving current behaviour. The context being *set but empty* (`[]`) is
distinct from *unset* and still engages the NER residual pass.

## R-04 · Call-context carrier

**WHEN** a caller needs candidate PII stripped for a specific LLM call, the system **SHALL**
provide a context manager (`dsm.pii.pseudonymised_lm.pii_context(known_pii)`) backed by a
`contextvars.ContextVar`, such that `PseudonymisedLM.__call__` reads the active known-PII list
from the context and the value is scoped to the `with` block (restored on exit, including on
exception). No known-PII list is passed as a function argument through `dsm.match`.

## R-05 · Query-time wiring sets the context per candidate (score)

**WHEN** the CLI composition root builds the score predictor, it **SHALL** wrap the injected
`ScorePredictor` so that, for each candidate, it resolves that candidate's known identifiers
from the vault (by `candidate_id`) and enters `pii_context(...)` around the LLM call. `dsm.match`
**SHALL** remain free of any `dsm.pii` import (the wrapper lives at the CLI, the same layer that
already owns `PseudonymisedLM` + `GoldCandidateStore`).

**WHEN** a candidate's `feedback`/`profile_summary` free-text (de-anonymised in gold) contains
that candidate's name, the system **SHALL** ensure that name does not reach the provider — the
seam input captured for the candidate is `candidate_id`/capability-only.

## R-06 · Minimal persistent vault — write path (ingest)

**WHEN** `dsm ingest` derives a `candidate_id` and its `(name, email)` from a supply row, the
system **SHALL** persist that identity into a vault store via `Vault.put_identity`, keyed by
`candidate_id`, to a **gitignored** location (default under `data/identity/`). The raw
`name`/`email` **SHALL NOT** be written to any committed artifact, logged, or placed in gold
(gold keeps `name_vault_ref`/`email_vault_ref` only, unchanged).

## R-07 · Minimal persistent vault — read path (query)

**WHEN** the query plane needs a candidate's known identifiers for redaction, the system
**SHALL** read them via a new `Vault.get_identity(candidate_id) -> tuple[str, str] | None`
(name, email). A missing candidate **SHALL** return `None` (→ empty known-PII list → NER-only
redaction, never a crash). The read path is the **only** new consumer of raw identity at query
time; identities **SHALL NOT** surface in any `ShortlistResult`/`NoMatchResult` field (output
stays pseudonymised this slice).

## R-08 · Vault persists across processes, encryption deferred

**WHEN** `dsm ingest` (one process) writes identities and a later `dsm match` (a separate
process) reads them, the system **SHALL** return the same `(name, email)` — i.e. the store is
**file-backed**, not in-memory-only. The store **MAY** be plaintext this slice; encryption,
retention limits, and purge-by-id are **explicitly deferred to AD-068 hardening** and the
module docstring + design **SHALL** state this with a TODO.

## R-09 · Deterministic, multi-fragment-stable redaction

**WHEN** a single LLM call spans multiple text fragments (prompt + several messages), the system
**SHALL** assign placeholders from one stable mapping so the same surface form maps to the same
placeholder across fragments (no cross-fragment collision), and the de-anonymise step restores
them unambiguously. For a fixed `(fragments, known_pii, ner)` the redaction **SHALL** be
byte-identical run-to-run (determinism invariant preserved).

## R-10 · no-PII-leak eval invariant exercises the real anonymiser

**WHEN** the `no-PII-leak` eval invariant (`dsm/eval/invariants.py`) runs over a golden case
whose candidate free-text contains a planted name, the system **SHALL** assert that the captured
seam inputs are free of that known PII **after** the real anonymiser runs — not structurally
only. The invariant docstring's structural-only TODO **SHALL** be updated to reflect that the
query-time anonymiser is now live (ingest-vs-query coverage stated precisely).

**WHEN** the anonymiser is bypassed (a deliberately-failing fixture feeding raw PII to the seam),
the invariant **SHALL** return `passed=False`.

## R-11 · Boundary + provider-path rules upheld

**WHEN** `make check` runs the import contracts, all four **SHALL** still pass: in particular
`dsm.match` remains free of `modal`/`httpx` (no direct provider) and the new wiring adds no
`dsm.match → dsm.pii` import. The provider is reached **only** through `PseudonymisedLM`
(`CLAUDE.md` golden rule 3 / `tech.md` rule 1).

## R-12 · Offline-testable, no live network in `make check`

**WHEN** the new behaviour is tested, redaction/leak-scan/vault tests and the
`PseudonymisedLM` wrapper test **SHALL** run with a fake inner LM (monkeypatched
`super().__call__`) and an injected fake NER — **no live OpenRouter/Modal call** under
`make check`. A Tier-3 live smoke (`eval_live`, key-gated `skipif`) **MAY** additionally verify
the real path end-to-end; it **SHALL** skip cleanly without keys.

## R-13 · Docs + decisions updated

**WHEN** the slice is complete, `docs/decision.md` **SHALL** carry **AD-101** (real
PseudonymisedLM query-time boundary) and **AD-102** (minimal persistent vault + `get_identity`),
`docs/tech.md` §PII **SHALL** reflect the live boundary + persistent vault (encryption still
deferred), and `docs/progress.C.md` **SHALL** be updated via `/handoff`. The shared index is
refreshed only at merge to `main` (`/handoff-index`).
