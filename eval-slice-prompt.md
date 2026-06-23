# Eval Harness — Implementation Slice Prompt (C-2)

> One execution prompt that wires the query-time **eval harness** described in
> `ee-query-architecture.md` §12 #9. Self-contained: scope, objectives, deliverables,
> acceptance criteria, and the spec files to write before any code. Companion to
> `query-slice-prompts.md` (B-1/B-2 built the pipeline this slice tests).

---

## Slice C-2: Query-Time Eval Harness — Invariants + Signature Regression + `make eval`

### Context

You are implementing the **eval harness** for the Demand–Supply Matcher. B-1/B-2
delivered the full 9-step query-time pipeline (parse → clarify → freshness → gate →
exact filter → recall → rerank → score → rank) and `dsm match` / `dsm explain`. This
slice does **not** add pipeline behaviour — it adds the automated checks that prove the
pipeline keeps its product invariants on every change, and replaces the `make eval`
stub (`Makefile` currently `echo SKIP; exit 1`).

Ownership is resolved in `ee-query-architecture.md` §12 #9: a **separate Lane-C slice
`c-002-query-eval-harness`**, sequenced **after B-2**. B-2 is **merged to `main` (PR #20)**
and added the eval *case list* to its `design.md` ("Eval cases to add"); this slice *wires*
those cases. The prerequisite pipeline is in place.

Before you begin: read `docs/progress.md` (index), `docs/progress.C.md` (your lane),
`CLAUDE.md` (the rules), `docs/decision.md`, `docs/structure.md`, `docs/tech.md`
(§Eval), `ee-query-architecture.md` (§12 #9, "Query-time quality metrics"), and the B-2
spec (`specs/b-002-query-scoring-orchestration/design.md` — the eval-case list).

### Design stance (decided — confirm at the gate)

- **Code-based evaluators first.** Five of the six invariants are *objective properties
  of the output* → deterministic Python assertions, the cheapest and most reliable tier.
  Do **not** use an LLM judge for any of them (an LLM judge on objective criteria is an
  anti-pattern). The one genuinely fuzzy check — narrative faithfulness — uses an LLM
  judge and is **deferred** (see Out of scope).
- **The e2e pipeline runs LLM-free in evals** via the injectable `predict` seam B-2
  already built for `clarify`/`score`. A recorded **cassette LM** makes every invariant
  eval deterministic and **key-free**, so the deterministic tier can gate every commit.
- **Two cost tiers, split by harness target:**
  - `make check` → Tier 1 deterministic invariant evals (cassette LM, no network).
  - `make eval` / `make check-all` → Tier 2 signature regression + Tier 3 live smoke
    (real OpenRouter/Modal; key-gated `skipif`).

#### Cassette discipline (pin this in `design.md`)

A cassette is a checked-in recording of the `clarify`/`score` `predict` outputs for a
golden case, replayed through the existing seam. To stop cassettes silently rotting into
fiction:

- **Location + format:** `tests/fixtures/cassettes/<case_id>/{clarify,score}.json`, one file
  per signature call, checked into git. Keyed by `(case_id, signature, prompt_hash,
  model_version, prompt_version)` — the same `(gold_hash, model_version)`-style derivation
  key ingestion already uses (AD-066/082).
- **Regeneration:** one command (e.g. `make eval-record` / `uv run python -m dsm.eval.record`)
  re-runs the **live** LM over the golden cases and rewrites the cassettes. Regeneration is
  explicit and reviewed in the diff — never automatic, never inside a test run.
- **Staleness is loud, never silent.** A cassette whose key no longer matches the current
  prompt/model version **fails the Tier-1 test with a "stale cassette — re-record" message**;
  it does **not** fall back to a live call and it does **not** pass on the old recording. A
  missing cassette is a hard error, not a skip.
- **Drift guard:** a Tier-3 (key-gated) test re-records into a temp dir and diffs against the
  committed cassettes, flagging when the live model has drifted from the recording the
  deterministic tier trusts. This is the bridge between "cheap + deterministic" and "still true."

### Scope

| Tier | Name | Ref | Module | Status |
|------|------|-----|--------|--------|
| 1 | **Invariant evaluators** (the six) | arch §12 #9; b-002 design "Eval cases" | `dsm/eval/invariants.py` | NEW |
| 1 | **Golden cases + cassette LM** | §6.0; `product.md` DoD | `dsm/eval/cases.py` + `tests/fixtures/` | NEW + ENRICH |
| 1 | **Tier-1 runner** | — | `tests/eval/test_invariants.py` | NEW |
| 2 | **Signature regression** (`clarify`/`score`) | `tech.md` §Eval | `tests/eval/test_signatures.py` | NEW |
| 3 | **Live smoke** | — | `tests/eval/test_live_smoke.py` | NEW (key-gated skip) |
| — | **`make eval` wiring + check-all gate** | `Makefile` | `Makefile` | REPLACE stub |

**Out of scope for C-2:**
- **Narrative-faithfulness LLM judge (G-Eval).** Not in the required six; an unvalidated
  judge is worthless (needs labelled data + TPR/TNR ≥ 80% validation, not available at
  POC scale). Document it as a follow-on; do **not** wire it.
- **Retrieval-quality metrics** (Recall@K, contextual precision/recall) — only meaningful
  once hybrid recall flips ON (AD-089, currently OFF/exhaustive). Note as future work.
- **Synthetic-data generator.** Golden cases are small + hand-authored (see Deliverable 5).
- **Observability platform / online monitoring** (Phoenix/Langfuse/`judgy`) — beyond POC.
  The lighter structlog quality metrics (arch "Query-time quality metrics") already exist.
- **Building the live `PseudonymisedLM` Presidio anonymiser** — that's a separate Lane-C task
  (`docs/progress.C.md`). C-2 does not build it. (Its consequence for *this* slice — that the
  no-PII-leak invariant can only assert structurally for now — is captured as an acceptance
  criterion, not waved off here.)

### Objectives

1. **Implement the six invariant evaluators** as pure, importable functions over a
   `ShortlistResult` / `NoMatchResult` (+ the captured seam inputs), each returning a
   pass/fail with a reason:
   - **gates-respected** — no candidate in `exclusion_log` appears in `ranked_assessments`;
     ROLE-01 Aarav absent (availability); ROLE-02 only Chennai-based/open present.
   - **hard-skill-not-cleared-by-adjacency** — an adjacent-but-missing-hard-skill candidate
     is in `exclusion_log` with `HARD_SKILL_MISMATCH`, never ranked.
   - **evidence-cited** — every `CandidateAssessment.evidence[].text` is verbatim-present in
     the candidate source (reuse the AD-073 verify).
   - **no-PII-leak** — run `dsm/pii/leakscan.assert_no_leak` over the inputs handed to the
     `predict`/embed/rerank seams and over the final output; assert capability/`candidate_id`-only.
   - **determinism** — *not* "run twice, same cassette, same output" (tautological: a fixed
     cassette + pure Python is identical by construction). It must test the **real**
     determinism risk — order- and seed-dependence in the deterministic plane. **Perturb the
     inputs without changing their meaning** (shuffle the candidate-list order, vary dict/set
     insertion order, re-seed) and assert the `ShortlistResult` is byte-identical: same ranking,
     tie-breaks, flags, and combine arithmetic regardless of input ordering. The cassette holds
     the *LLM* fixed so this isolates the plumbing. Live-model reproducibility at `temperature=0`
     is a **separate Tier-3 concern** (and the cassette drift-guard above), called out as such —
     the cassette tier cannot and does not claim to test it.
   - **adjacency-flag** — `ADJACENCY_USED` present iff adjacency credit was awarded.
2. **Build golden cases + a cassette LM** for ROLE-01/02/03 + negative (no-match) cases:
   the parsed/scorecard inputs (reuse `tests/fixtures`), the recorded `clarify`/`score`
   responses, and the **expected shortlist / no-match** for each. Include should-fail cases
   (`product.md`: "100% pass = insufficient coverage").
3. **Enrich the seed fixtures with source evidence** (Deliverable 5) so `evidence-cited`
   and the AI scoring step have real resume/feedback text to quote and reason over.
4. **Tier-2 signature regression** — pin `clarify` and `score` DSPy signatures against fixed
   inputs: sub-scores ∈ [0,1], a citation present, hard skill never credited via adjacency in
   the raw LLM output.
5. **Tier-3 live smoke** — one real-LLM pass over a seed role asserting a well-formed
   `ShortlistResult`; `skipif` when keys are absent (never fails a key-less CI).
6. **Wire `make eval`** — replace the `exit 1` stub; run Tier 1 in `make check`, Tiers 2–3
   in `make eval`; `make check-all = check + eval` becomes the green gate.

### Deliverables

#### 1. Spec files (write first, stop for review)

Create `specs/c-002-query-eval-harness/` with `requirements.md` → `design.md` →
`tasks.md` (format in `docs/structure.md`; precedent `specs/b-002-query-scoring-orchestration/`).
**First task is `T-000-ADR`** — STOP for sign-off before any code.

- **`requirements.md`** — EARS-form acceptance criteria, one per invariant, each referencing
  the product invariant (`product.md` §"Product invariants") and the arch §12 #9 case list.
- **`design.md`** — module signatures for the six evaluators, the cassette-LM seam, the
  golden-case shape, the fixture-enrichment plan, the `check`/`check-all` split, and the
  `promptfoo` packaging decision.
- **`tasks.md`** — ordered, atomic, independently testable; one task = one commit.

#### 2. ADRs to ratify (in `docs/decision.md`; **next IDs start at AD-093** — verify: B-2 ratified AD-089…092)

- **`T-000-ADR` (gate task — do first, STOP for human sign-off):**
- **AD-093 — Eval harness architecture: code-based-first tiering + `promptfoo` packaging.**
  The pinned `promptfoo` (PyPI `0.1.4`, a 17 KB placeholder) is **not** the real tool (npm;
  `npx` is on PATH). **Decide:** (a) drop the PyPI dep and run signature regression as
  DeepEval/pytest cases (recommended — one framework, no node toolchain in CI), or (b) drive
  real promptfoo via `npx`. Either changes `docs/tech.md` §Eval → needs this ADR. Records the
  three-tier model (deterministic code-based / signature regression / live smoke + deferred judge).
- **AD-094 — `make check` vs `make check-all` eval split (with the exact collection rule).**
  Records *which tests run where* via a pytest marker scheme, not just "deterministic vs LLM":
  - **`make check`** collects: all unit tests **plus** `tests/eval/test_invariants.py` (Tier 1,
    marked `@pytest.mark.eval_offline`). These use the cassette LM, touch no network, need no keys.
  - **`make eval`** collects **only** `-m "eval_offline or eval_live"` under `tests/eval/`:
    Tier 1 (re-run), Tier 2 signature regression (`eval_offline`), and Tier 3 live smoke +
    cassette drift-guard (`eval_live`, `skipif` no keys).
  - **`make check-all`** = `make check` + `make eval`.
  - Markers registered in `pyproject.toml` (`[tool.pytest.ini_options] markers`); `eval_live`
    tests carry `@pytest.mark.skipif(not _has_keys(), ...)`. The rationale (code-based evals are
    fast/free/key-free → gate every commit; live calls cost money + need secrets → opt-in) is
    recorded so the boundary isn't re-litigated.

#### 3. Implementation modules

| File | What it does |
|------|-------------|
| `dsm/eval/invariants.py` (NEW) | Six pure functions `f(result, *, seam_inputs=None) -> InvariantResult(passed: bool, reason: str)`. Importable + reusable; no test framework imports. Reuse `dsm/pii/leakscan` for no-PII-leak and the AD-073 quote-verify for evidence-cited. |
| `dsm/eval/cases.py` (NEW) | Golden cases: each binds a seed role (reuse `tests/fixtures.role_01/02/03`) to its cassette LM responses + expected `ShortlistResult`/`NoMatchResult`. Includes negative/should-fail cases. |
| `tests/fixtures/__init__.py` (ENRICH) | Add small hand-authored resume + feedback source text + non-empty `FeedbackSignals` to the seed candidates (Deliverable 5) so citations resolve and `score` has real input. Keep the existing gates/rank fixtures green. |
| `tests/eval/test_invariants.py` (NEW) | Tier-1 runner: drive `run_match` (cassette LM, temp 0) over each golden case; assert all six invariants. Deterministic, no network — runs under `make check`. |
| `tests/eval/test_signatures.py` (NEW) | Tier-2 signature regression for `clarify`/`score` (cassette or real, per AD-093). `make eval`. |
| `tests/eval/test_live_smoke.py` (NEW) | Tier-3 real-LLM e2e over one seed role; `pytest.mark.skipif` on missing keys. `make eval`. |
| `tests/eval/conftest.py` (NEW) | **Teeth for "no live provider in `make check`":** an `autouse` fixture, active for `eval_offline` tests, that patches the provider entry points (`ModalEmbedClient`, the OpenRouter/DSPy LM constructor in `PseudonymisedLM`) to **raise `RuntimeError("live provider called in offline eval")`** if instantiated or called. A missing/forbidden network call fails loudly rather than silently degrading. (Homegrown — no new dep like `pytest-socket`, which would need an ADR.) The same guard asserts the injected `predict`/`EmbedClient` are the cassette/fake doubles, not real clients. |
| `Makefile` (REPLACE) | `eval` target → `uv run pytest tests/eval -m "eval_offline or eval_live"`; drop the `exit 1` stub. `make check` additionally collects `tests/eval/test_invariants.py` (Tier-1, `eval_offline`). |
| `dsm/eval/README.md` (UPDATE) | Replace the "not configured" note with the tier model + how to run. |
| `pyproject.toml` (per AD-093) | Drop the `promptfoo` placeholder dep if option (a) is chosen; add a `eval` pytest marker. |

#### 4. Tests

The eval suite *is* the test surface. Additionally:

| Test file | Coverage |
|-----------|----------|
| `tests/eval/test_invariants.py` | All six invariants × {ROLE-01, ROLE-02, ROLE-03, negative cases}; each invariant has at least one **passing** and one deliberately **failing** fixture (proves the evaluator detects breakage, not just absence — see example below) |

**Deliberately-failing fixture — worked example (gates-respected).** The risk with a
pass-only suite is an evaluator that always returns `passed=True`. So each invariant ships a
**tampered `ShortlistResult`** that violates it, and the test asserts the evaluator returns
`passed=False` with the right reason:

```python
def test_gates_respected_detects_a_gated_candidate_in_the_shortlist():
    # ROLE-01: Aarav is gated out on availability (RollingOff +17d past the window).
    base = golden_case("ROLE-01").expected_shortlist
    aarav = excluded_assessment(base, email="aarav@example.com")  # fabricate a ranked entry
    tampered = base.model_copy(update={
        "ranked_assessments": [aarav, *base.ranked_assessments],   # inject the gated candidate
    })
    result = gates_respected(tampered)
    assert result.passed is False
    assert "aarav@example.com" in result.reason          # names the offender
    assert "AVAILABILITY_MISMATCH" in result.reason       # and why it was gated
```

Mirror this for the other five (e.g. *evidence-cited* → mutate one `evidence[].text` to a
quote absent from source, assert it's flagged; *adjacency-flag* → award adjacency credit but
strip `ADJACENCY_USED`, assert mismatch). The passing fixture is the untampered golden case.
| `tests/eval/test_signatures.py` | `clarify`/`score` output well-formedness: sub-scores ∈ [0,1], citation present, no adjacency credit on a hard skill |
| `tests/eval/test_live_smoke.py` | Real-LLM `ShortlistResult` well-formedness; skipped without keys |

#### 5. Data: fixture enrichment (the "do we need synthetic data?" answer)

**No synthetic-data generator.** Deterministic invariant evals need *known, hand-verified*
ground truth — generated data has none. We need a small, curated, hand-authored set:

- The seed fixtures (`tests/fixtures/__init__.py`) were built for gates/rank: every candidate
  has empty `FeedbackSignals()`, one skill, and **no source text**. That covers
  gates-respected / determinism / adjacency, but leaves `evidence-cited` with nothing to
  verify and `score` with no input.
- **Add** a few short resume + feedback snippets (a dozen lines across the three roles) and
  populated `FeedbackSignals`, so citations point to real text and the AI step has substance.
  Hand-author it — it must be *trusted* ground truth.
- **Record** cassette `clarify`/`score` responses for each golden case (once, from a real run
  or hand-written) so the e2e evals are deterministic + key-free.
- The untracked `data/raw/demand/open_roles.csv` exercises the full parse→shortlist path.

> Building these golden fixtures **is the first implementation task**, not a blocker before
> the slice. It is curation, not generation.

### Acceptance criteria

- [ ] `T-000-ADR` ratified (AD-093/094) in `docs/decision.md` before any dependent code
- [ ] `make check` GREEN — Tier-1 invariant evals included, deterministic, **no network/keys**
- [ ] `make eval` runs (no longer `exit 1`); `make check-all` (= `check` + `eval`) is the gate
- [ ] All six invariants pass on ROLE-01/02/03 + negative cases
- [ ] Each invariant has a deliberately-failing fixture that it correctly flags `passed=False` with a reason (detects breakage, not just absence)
- [ ] **determinism** asserts ordering/seed invariance (perturbed input → byte-identical output), **not** cassette-replay equality; the doc states the cassette tier does not test live-model `temperature=0` reproducibility (Tier-3 + drift-guard does)
- [ ] **no-PII-leak asserts structurally only** — because `PseudonymisedLM` is still a pass-through stub (Lane C, `docs/progress.C.md`), the invariant verifies seam inputs are capability/`candidate_id`-only; it does **not** yet exercise a real anonymiser. This limitation is stated in the invariant's docstring + the spec, with a TODO to tighten when the live anonymiser lands.
- [ ] Seed fixtures enriched with source evidence; `evidence-cited` verifies real quotes; existing gates/rank tests still pass
- [ ] Cassette discipline holds: cassettes checked in + keyed by `(case_id, signature, prompt_hash, model_version, prompt_version)`; a stale/missing cassette **fails loudly** (no live fallback, no skip); `make eval-record` regenerates explicitly
- [ ] Tier-2 signature regression pins `clarify`/`score` well-formedness
- [ ] Tier-3 live smoke + cassette drift-guard run with keys, **skip cleanly without them** (never red on key-less CI)
- [ ] **No live provider can be called under `make check`** — the `tests/eval/conftest.py` autouse guard patches `ModalEmbedClient` + the OpenRouter LM constructor to raise; a test proves the guard fires (instantiating a real client in an `eval_offline` test errors)
- [ ] `make eval` / `make check / check-all` collect exactly the marker sets AD-094 specifies
- [ ] `promptfoo` packaging resolved per AD-093 (placeholder dropped if option (a)); `docs/tech.md` §Eval synced
- [ ] No new dependencies beyond `docs/tech.md` (or an ADR if AD-093 changes them)
- [ ] `docs/progress.C.md` updated via `/handoff`; `docs/decision.md` carries AD-093/094 (confirmed next-free: decision.md ends at AD-092, "Next ADRs start at AD-093")

### Constraints

- **Spec before code.** Write `specs/c-002-query-eval-harness/{requirements,design,tasks}.md`
  and stop for sign-off (Golden rule 1).
- **`T-000-ADR` first** — the `promptfoo` packaging + `check`/`check-all` split decided before
  dependent code.
- **One task = one commit**, imperative, referencing the spec.
- **No LLM judge for the six invariants** — code-based assertions only. The narrative judge is
  deferred (Out of scope), documented as a follow-on.
- **Deterministic tier is key-free** — Tier 1 must run under `make check` with the cassette LM;
  no live OpenRouter/Modal call in `make check`.
- **Never disable or weaken a check to make it pass** (Golden rule / `CLAUDE.md`). If an
  invariant catches a real pipeline bug, fix the pipeline (a separate Lane B/C concern) — don't
  relax the eval.
- **Reuse, don't duplicate** — seed fixtures, `leakscan`, the AD-073 quote-verify, `run_match`.

---

## Dependency graph

```
B-2 (full 9-step pipeline + run_match/explain)   ← prerequisite, green
        │
        ▼
C-2 (eval harness)
 ├── dsm/eval/invariants.py   — six code-based evaluators
 ├── dsm/eval/cases.py        — golden cases + cassette LM
 ├── tests/fixtures/          — enriched with source evidence
 ├── tests/eval/              — Tier 1 (check) + Tier 2/3 (eval)
 └── Makefile                 — make eval wired; check-all gates
```

C-2 adds no pipeline behaviour; it locks the product invariants in CI. Deferred: the
narrative-faithfulness LLM judge (+ its labelled-set validation) and retrieval-quality
metrics (when AD-089 recall flips ON).
