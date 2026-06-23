# Tasks — c-003 Real PseudonymisedLM Boundary

> Ordered, atomic, independently testable. One task = one commit, imperative, referencing the
> spec. **First task is T-000-ADR — STOP for human sign-off before any code.**

---

## T-000-ADR — Ratify AD-101/098 (GATE — stop for human sign-off)

- Append **AD-101** (real PseudonymisedLM query-time boundary: call-context `known_pii`,
  redact-first + NER + leak-scan over prompt+messages, de-anonymise response, unset-context
  pass-through, CLI-level wiring keeps `dsm.match` pii-free) and **AD-102** (minimal persistent
  `FileVault` + `get_identity`; plaintext now, encryption/retention/purge deferred to AD-068) to
  `docs/decision.md`. Next IDs start at **AD-101** (verified: `decision.md` ends at AD-096).
- Update `docs/tech.md` §PII: boundary is live; identity in a persistent (gitignored) vault;
  encryption still deferred.
- `make check` GREEN (docs-only).
- **STOP — human sign-off before proceeding.**

**AC:** R-13. AD-101/098 in `decision.md`; `tech.md` §PII synced; `make check` green.

---

## T-001 — Stable multi-fragment redaction (`Redactor`)

- Add `Redactor` + `redact_fragments` to `dsm/pii/redact.py`; reimplement `redact()` in terms of
  `Redactor` so there is one code path. Known-PII placeholders stay index-stable; the NER
  residual pass reuses a cumulative `surface→placeholder` map across fragments.
- Tests: same surface form → same placeholder across 3 fragments; no `NER_k` collision; a fixed
  `(fragments, known_pii, ner)` is byte-identical run-to-run; existing `redact`/`deanonymize`
  tests still pass.
- `make check` GREEN.

**AC:** R-09. Cross-fragment-stable, deterministic; single-text path unchanged.

---

## T-002 — `pii_context` + real `PseudonymisedLM.__call__`

- Add `_KNOWN_PII: ContextVar` + `pii_context()` to `dsm/pii/pseudonymised_lm.py`.
- Replace the no-op `__call__`: unset context → pass-through; set context → redact `prompt` +
  each message `content` under one `Redactor`, `assert_no_leak` each fragment (hard gate), forward,
  de-anonymise the response. Handle both `prompt` and `messages` shapes (DSPy 3.x).
- Inject the NER seam (`self._ner`, default `redact._default_ner`) so tests pass a fake.
- Tests (fake inner LM via monkeypatched `super().__call__`, fake NER): redacts + restores;
  residual known PII → `PIILeakError` and inner LM never called; unset context → byte-identical
  pass-through.
- `make check` GREEN (no live network).

**AC:** R-01/02/03/04/12.

---

## T-003 — Persistent `FileVault` + `get_identity`

- Add `get_identity(candidate_id) -> tuple[str,str] | None` to the `Vault` Protocol.
- Implement `FileVault` (JSON file at a given path; upsert + flush on `put_identity`; `get_identity`
  returns `None` for a missing id). Docstring states plaintext + deferred encryption (AD-102).
- Keep `InMemoryVault`; add `get_identity` to it too.
- Tests: put→get round-trip; **cross-instance** (new `FileVault` same path reads prior writes);
  missing id → `None`; blank inputs handled.
- `make check` GREEN.

**AC:** R-06/07/08.

---

## T-004 — Wire ingest write path + config + gitignore

- In the ingest CLI flow (`dsm/cli/commands.py`, where `known_pii[cid] = [name, email]` is built),
  construct a `FileVault(config.pii.vault_path)` and `put_identity(cid, name, email)` per candidate.
- Add `pii.vault_path` (default `data/identity/vault.json`) and optional `pii.ner_enabled` to
  `config/default.yaml`; add `data/identity/` to `.gitignore`.
- Verify no raw name/email is logged or written to gold (gold refs unchanged).
- Tests: after an ingest run (existing fixtures), the vault file exists and `get_identity` returns
  the seeded identities; gold still carries only `*_vault_ref`.
- `make check` GREEN.

**AC:** R-06/08. Ingest persists identity; gitignored; gold unchanged.

---

## T-005 — Wire query read path (CLI score-predictor wrapper)

- At the CLI composition root, wrap the injected `ScorePredictor`: resolve
  `vault.get_identity(candidate.email)` → `known_pii`, enter `pii_context(known_pii)` around the
  base predictor call. `clarify` predictor is left **unwrapped** (no context → pass-through).
- Confirm `dsm/match/*` gains **no** `dsm.pii` import (import contracts green).
- Tests (CLI, cassette/fake LM): a candidate whose hydrated `feedback`/`profile_summary` contains
  a planted name → the text reaching the (captured) seam is PII-free; ranking output unchanged
  vs the pre-wiring run for PII-free candidates.
- `make check` GREEN.

**AC:** R-05/11.

---

## T-006 — Tighten the `no-PII-leak` eval invariant

- Extend the c-002 fixtures/cassettes: one candidate's `profile_summary`/feedback gains a planted
  name; cassette `score` response references the de-anonymised quote (so `evidence-cited` still
  passes after restore).
- Update `dsm/eval/invariants.py::no_pii_leak` to assert seam inputs are PII-free **after** the
  real anonymiser (not structural-only); update the docstring TODO (query-time anonymiser now live;
  state precisely what is/ isn't covered).
- Add the deliberately-failing fixture (bypass the anonymiser → `passed=False`).
- `make check` GREEN (Tier-1 included); existing eval tests still pass.

**AC:** R-10/15-style (deliberately-failing fixture).

---

## T-007 — Tier-3 live smoke (optional path)

- Add/extend `tests/eval/test_live_smoke.py` (`eval_live`, `skipif` no keys): one real-LLM pass
  with `pii_context` active over a planted-name candidate; assert well-formed `ShortlistResult`
  and (best-effort) no planted name in the captured outbound text.
- `make check` GREEN (skips without keys); `make eval` runs it with keys.

**AC:** R-12.

---

## T-008 — `/handoff` — update `docs/progress.C.md`

- Run `/handoff` (set `.claude/lane` to `C`) to update `docs/progress.C.md` session log + Next up.
- Verify every `requirements.md` AC is met; `make check` GREEN; `make check-all` GREEN (or
  `eval_live` skips cleanly).

**AC:** R-13. Lane file current; slice complete.

---

## Task dependency graph

```
T-000-ADR (gate — STOP)
    │
    ├── T-001 (Redactor) ── T-002 (pii_context + PseudonymisedLM)
    │                              │
    ├── T-003 (FileVault) ─────────┤
    │       │                      │
    │       ├── T-004 (ingest write + config + gitignore)
    │       │
    │       └── T-005 (CLI query wiring)  ←── needs T-002 + T-003
    │                   │
    │                   └── T-006 (no-PII-leak invariant)  ←── needs T-005
    │                           │
    │                           └── T-007 (Tier-3 live smoke)
    │
    └────────────────────────────────── T-008 (/handoff)
```
