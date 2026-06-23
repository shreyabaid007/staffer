# Design — c-003 Real PseudonymisedLM Boundary

> Modules touched, data contracts, the call flow, edge cases, and the eval cases to add.
> Implements the decisions confirmed with the human:
> 1. **Approach A** — call-context `known_pii` + vault read (preserves AD-069's deterministic
>    redact-first guarantee at query time).
> 2. **Scope** — real anonymise/de-anonymise in `PseudonymisedLM` only (no output
>    de-pseudonymisation, no encrypted vault, no generic index-path NER scan).
> 3. **Minimal persistent vault** — ingest writes a gitignored file store, query reads it;
>    encryption/retention/purge deferred to AD-068.

---

## 1. The problem this closes (why now)

`PseudonymisedLM` is a no-op pass-through ([`dsm/pii/pseudonymised_lm.py`](../../dsm/pii/pseudonymised_lm.py)).
Ingest's `enrich` already runs the *real* boundary itself (`redact → assert_no_leak → LLM →
deanonymize`, [`dsm/ingest/enrich.py`](../../dsm/ingest/enrich.py)), so the stub is harmless there.
But **query/score time leaks**:

- [`dsm/cli/store.py:118-127`](../../dsm/cli/store.py#L118-L127) hydrates a serving `Candidate`
  with `feedback.text = gold feedback summary` and `profile_summary = " ".join(gold.projects)`.
  **Gold free-text is de-anonymised** (enrich restores real names before writing gold).
- [`dsm/match/score.py:69-76`](../../dsm/match/score.py#L69-L76) sends `candidate_feedback` +
  `profile_summary` to the LLM via the injected predictor over `PseudonymisedLM`.

With the no-op stub, a name embedded in feedback/projects reaches OpenRouter. `clarify`
([`dsm/match/clarify.py`](../../dsm/match/clarify.py)) is genuinely role-only (§7) — no candidate
PII — so it must keep working unchanged (pass-through).

The deterministic known-identifier strip is AD-069's load-bearing guarantee (NER alone is
distrusted for Indian surnames / org names). The wrapper only sees a formatted prompt, so the
candidate's identifiers must be supplied via a **call-context** set by the composition root,
which resolves them from a **persistent vault** (gold carries only `name_vault_ref` pointers,
and nothing persists identity across the ingest→query process boundary today).

---

## 2. Modules touched

| File | Change | AC |
|------|--------|----|
| `dsm/pii/redact.py` | ADD a `Redactor` session (stable cross-fragment mapping) + `redact_fragments`; keep existing `redact`/`deanonymize` (reuse, no behaviour change to the single-text path) | R-09 |
| `dsm/pii/pseudonymised_lm.py` | REPLACE no-op `__call__`: read call-context → redact `prompt`+`messages` → `assert_no_leak` → forward → de-anonymise response. ADD `pii_context()` context manager + `ContextVar` | R-01/02/03/04 |
| `dsm/pii/vault.py` | ADD `get_identity` to the `Vault` Protocol; ADD `FileVault` (persistent, gitignored, plaintext now); keep `InMemoryVault` for tests | R-06/07/08 |
| `dsm/cli/store.py` or `dsm/cli/commands.py` | WIRE: ingest writes identities to `FileVault`; query wraps the score predictor with vault lookup + `pii_context` | R-05/06/07 |
| `dsm/eval/invariants.py` | TIGHTEN `no_pii_leak` (real-anonymiser assertion + planted-name golden case); update docstring TODO | R-10 |
| `.gitignore` | ADD the identity store path (`data/identity/`) | R-06 |
| `config/default.yaml` | ADD `pii.ner_enabled` (NER seam toggle) | R-06/07 |

> **Refinement (during impl):** the vault path is a **`--vault-path` CLI option** on
> `ingest`/`match`/`explain` (mirrors `--gold-dir`), defaulting alongside gold at
> `<gold_dir>/../identity/vault.json` (→ `data/identity/vault.json` in prod). This is more
> consistent with the codebase (bronze/silver/gold/raw are all CLI options, not config) and keeps
> tests hermetic for free — overriding `--gold-dir` to a tmp dir moves the vault with it. Config
> carries only the behavioural `pii.ner_enabled` toggle, not the path.
| `docs/decision.md` | AD-101, AD-102 | R-13 |
| `docs/tech.md` | §PII reflects live boundary + persistent vault | R-13 |

**Unchanged on purpose:** `dsm/match/*` (stays `dsm.pii`-free — wiring is at the CLI); gold schema
(`name_vault_ref`/`email_vault_ref` pointers unchanged); `dsm/index/*`.

---

## 3. Data contracts

```python
# dsm/pii/redact.py — stable multi-fragment redaction
class Redactor:
    """Holds one cumulative placeholder→original mapping across many fragments, so the same
    surface form yields the same placeholder everywhere in one LLM call (R-09)."""
    def __init__(self, known_pii: list[str], ner: NerFn | None = None) -> None: ...
    def redact(self, text: str) -> str: ...          # mutates the shared mapping
    @property
    def mapping(self) -> dict[str, str]: ...          # placeholder → original (in-memory only)

def redact_fragments(texts: list[str], *, known_pii, ner=None) -> tuple[list[str], dict[str,str]]:
    """Convenience: redact a batch with one Redactor; returns (redacted_texts, mapping)."""
```

Implementation note: the **known-PII pass is already index-stable** (placeholder `PII_i` is keyed
to position `i` in the longest-first-sorted `known_pii`, independent of which fragment it appears
in). The fix is the **NER residual pass**: the `Redactor` keeps a `surface→placeholder` reverse
map so a residual span seen in fragment 2 reuses the placeholder it (or the known pass) got in
fragment 1, and new spans get the next global `NER_k`. Single-text `redact()` is reimplemented as
`Redactor([...]).redact(text)` to avoid two code paths.

```python
# dsm/pii/pseudonymised_lm.py
_KNOWN_PII: ContextVar[list[str] | None] = ContextVar("dsm_pii_known", default=None)

@contextmanager
def pii_context(known_pii: list[str]) -> Iterator[None]:
    token = _KNOWN_PII.set(list(known_pii))
    try: yield
    finally: _KNOWN_PII.reset(token)

class PseudonymisedLM(dspy.LM):
    def __call__(self, prompt=None, messages=None, **kwargs):
        known = _KNOWN_PII.get()
        if known is None:                       # R-03 unset → pass-through (clarify)
            return super().__call__(prompt, messages=messages, **kwargs)
        r = Redactor(known, ner=self._ner)      # one mapping for the whole call (R-09)
        anon_prompt = r.redact(prompt) if prompt is not None else None
        anon_messages = _redact_messages(messages, r) if messages else None
        for frag in _fragments(anon_prompt, anon_messages):
            assert_no_leak(frag, known_pii=known)            # R-02 hard gate
        out = super().__call__(anon_prompt, messages=anon_messages, **kwargs)
        return _deanonymize_response(out, r.mapping)         # R-01 restore
```

```python
# dsm/pii/vault.py
class Vault(Protocol):
    def put_identity(self, candidate_id, name, email) -> tuple[str, str]: ...
    def get_identity(self, candidate_id: str) -> tuple[str, str] | None: ...   # NEW (R-07)

class FileVault:
    """Persistent identity store keyed by candidate_id (R-08). JSON file at vault_path,
    gitignored. PLAINTEXT this slice — TODO(AD-068): encrypt at rest + retention + purge-by-id."""
    def __init__(self, path: Path) -> None: ...
    def put_identity(self, candidate_id, name, email) -> tuple[str, str]: ...   # upsert + flush
    def get_identity(self, candidate_id) -> tuple[str, str] | None: ...
```

---

## 4. Call flow

```
ingest (process 1):  supply row → (name, email, cid) → FileVault.put_identity(cid,...)  [R-06]
                     gold keeps name_vault_ref/email_vault_ref only (unchanged)

match (process 2):
  CLI builds: base = make_score_predictor(PseudonymisedLM(reasoning_llm))
              vault = FileVault(config.pii.vault_path)
              def score_predict(scorecard, candidate):              # the injected seam
                  ident = vault.get_identity(candidate.email)       # email == candidate_id (AD-091)
                  known = [p for p in ident or () if p]             # [] when missing → NER-only
                  with pii_context(known):                          # [R-04/R-05]
                      return base(scorecard, candidate)
  run_match(..., score_predict=score_predict)   # dsm.match unchanged, still pii-free [R-11]
      → score.score_candidate calls score_predict(...) per candidate
          → dspy.Predict calls PseudonymisedLM(messages=...)        # context active
              → redact + leak-scan + call + de-anonymise            # [R-01/02/09]

clarify: built over PseudonymisedLM but invoked with NO pii_context → pass-through [R-03]
```

`candidate.email` carries the pseudonymised `candidate_id` (AD-091, [`store.py:119`](../../dsm/cli/store.py#L119)),
which is exactly the vault key — no raw identity is needed by `dsm.match` to drive the lookup.

---

## 5. Edge cases

- **Missing vault entry** (thin profile, or query against gold built before the vault existed):
  `get_identity → None → known=[] → NER-only`. Never crashes (R-07). Logged as a debug count.
- **NER model absent** (`en_core_web_lg` not downloaded): `_default_ner` already degrades to `[]`
  ([`redact.py:116-134`](../../dsm/pii/redact.py#L116-L134)). The deterministic vault-backed pass
  remains the load-bearing guarantee; leak-scan still gates known PII. Tests inject a fake NER.
- **Model echoes a placeholder inside a larger token** — `deanonymize` is a literal replace of
  the bracketed `[[PII_i]]`/`[[NER_k]]` token; placeholders are bracketed to avoid substring hits.
- **`prompt` and `messages` both present** — both redacted under one mapping; both leak-scanned.
- **Empty context `[]` vs unset `None`** — `[]` engages NER (set by score when vault returns no
  identity but we still want residual NER); `None` is full pass-through (clarify). R-03.
- **Exception inside the `with` block** — `pii_context` resets the ContextVar in `finally`
  (no leakage of one candidate's known list into the next call).
- **Determinism** — one `Redactor` per call, deterministic ordering; the eval `determinism`
  invariant (byte-identical output under input reordering) must stay green (R-09).

---

## 6. Security / boundary notes

- The pseudonym mapping is **in-memory only** for the LLM call (AD-010); the *persistent* store
  is identity (cid→name/email), not the per-call placeholder map. Neither is ever logged
  (`tech.md`). Leak-scan/PIILeakError report counts, not values.
- `FileVault` plaintext is a **known, signed-off limitation** (R-08): it lives gitignored under
  `data/identity/`, and AD-102 records encryption/retention/purge as deferred to the AD-068
  hardening slice. This does not weaken the *outbound* guarantee (redact-first + leak-scan); it
  only stores at-rest identity that previously lived in-process during ingest.
- `dsm.match` stays import-clean of `dsm.pii` (R-11): the only new coupling is at the CLI
  composition root, which already imports both `dsm.pii` and `dsm.ingest`.

---

## 7. Eval cases to add

| Tier | Case | Asserts | AC |
|------|------|---------|----|
| unit | `redact_fragments` stable mapping | same surface → same placeholder across 3 fragments; byte-identical re-run | R-09 |
| unit | `PseudonymisedLM` redacts + restores | fake inner LM sees only redacted text; response placeholders restored | R-01 |
| unit | `PseudonymisedLM` leak gate | residual known PII → `PIILeakError`, inner LM never called | R-02 |
| unit | `PseudonymisedLM` unset context | no context → byte-identical pass-through (clarify) | R-03 |
| unit | `FileVault` round-trip + cross-instance | put then get (new instance, same path) returns identity; missing → None | R-07/R-08 |
| Tier-1 (`eval_offline`) | `no_pii_leak` planted-name golden case | gold feedback with a planted name → seam input PII-free after anonymiser; tampered (bypass) → `passed=False` | R-10 |
| Tier-3 (`eval_live`, skipif) | live smoke over one role with real OpenRouter | well-formed `ShortlistResult`; skips without keys | R-12 |

The planted-name golden case extends the existing fixtures/cassettes from c-002 (one candidate's
`profile_summary`/feedback gains a name string; the cassette `score` response references the
de-anonymised quote so `evidence-cited` still passes after restore).

---

## 8. ADRs to ratify (T-000-ADR — STOP for sign-off)

- **AD-101 — Real PseudonymisedLM query-time boundary.** Call-context `known_pii` via a
  `ContextVar` (`pii_context`); redact-first (vault-backed deterministic strip) + NER residual +
  outbound leak-scan over both `prompt` and `messages`; de-anonymise the response; **unset
  context = pass-through** (clarify, §7). Wiring lives at the CLI composition root so `dsm.match`
  stays `dsm.pii`-free. Closes the query/score leak; satisfies AD-010/069 at query time.
- **AD-102 — Minimal persistent `FileVault` + `get_identity` read path.** Ingest persists
  cid→(name,email) to a gitignored plaintext JSON store; query reads it for redaction only.
  Adds `get_identity` to the `Vault` Protocol. **Encryption, retention limits, and purge-by-id
  are deferred to the AD-068 hardening slice** (recorded so it isn't re-litigated). Output stays
  pseudonymised this slice (no name de-pseudonymisation).
