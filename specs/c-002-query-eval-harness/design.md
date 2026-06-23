# Design — c-002 Query-Time Eval Harness

> How C-2 is built. Adds no pipeline behaviour — it wires the automated checks that prove
> the pipeline keeps its product invariants on every change, and replaces the `make eval`
> stub. Architecture: `ee-query-architecture.md` §12 #9, §9 "Query-time quality metrics";
> B-2 "Eval cases to add".

---

## Design stance (decided)

- **Code-based evaluators first.** Five of the six invariants are objective properties of
  the output → deterministic Python assertions. No LLM judge for any of them.
- **The e2e pipeline runs LLM-free in evals** via the injectable `predict` seam B-2 built
  for `clarify`/`score`. A recorded **cassette LM** makes every invariant eval
  deterministic and key-free.
- **Two cost tiers, split by harness target:**
  - `make check` → Tier 1 deterministic invariant evals (cassette LM, no network).
  - `make eval` / `make check-all` → Tier 2 signature regression + Tier 3 live smoke.

---

## ADRs to ratify (T-000-ADR gate — stop for sign-off)

### AD-095 — Eval harness architecture: code-based-first + promptfoo resolution

**Decision (a) — recommended:** Drop the `promptfoo` PyPI placeholder (0.1.4, a 17 KB
stub — not the real tool which is npm). Run signature regression as pytest cases (DeepEval
or plain pytest assertions). One framework, no node toolchain in CI.

Records the three-tier model:

| Tier | What | Runner | Keys? |
|------|------|--------|-------|
| 1 | Invariant evaluators (6) | `tests/eval/test_invariants.py`, `eval_offline` | No |
| 2 | Signature regression (`clarify`/`score`) | `tests/eval/test_signatures.py`, `eval_offline` | No (cassette) |
| 3 | Live smoke + cassette drift guard | `tests/eval/test_live_smoke.py`, `eval_live` | Yes (`skipif`) |

Deferred: narrative-faithfulness LLM judge (G-Eval) — needs labelled data + TPR/TNR ≥ 80%
validation. Documented as follow-on; not wired.

Changes `docs/tech.md` §Eval: replace "Promptfoo (signature-level) + DeepEval" with
"Code-based invariant evaluators (Tier 1) + pytest signature regression (Tier 2) +
live smoke (Tier 3)". Drop `promptfoo` from `pyproject.toml`. Keep `deepeval` available
but the Tier 1/2 evaluators are plain functions + pytest — no DeepEval dependency in the
hot path.

### AD-096 — `make check` vs `make check-all` eval split

**Marker scheme** (registered in `pyproject.toml [tool.pytest.ini_options] markers`):

- `eval_offline` — deterministic, no network, no keys. Cassette LM or fixed inputs.
- `eval_live` — needs API keys (OpenRouter/Modal). `@pytest.mark.skipif(not _has_keys())`.

**Collection rules:**

| Target | Collects | Rationale |
|--------|----------|-----------|
| `make check` | All unit tests + `tests/eval/test_invariants.py` (`eval_offline`) | Tier-1 is fast/free/key-free → gate every commit |
| `make eval` | `tests/eval/ -m "eval_offline or eval_live"` (Tiers 1+2+3) | Live calls cost money + need secrets → opt-in |
| `make check-all` | `make check` + `make eval` | The green gate |

---

## Modules

### `dsm/eval/invariants.py` (NEW)

Six pure functions, importable and reusable. No test-framework imports. Each takes a
pipeline result + optional context and returns an `InvariantResult`.

```python
from __future__ import annotations
from dataclasses import dataclass

MatchResult = ShortlistResult | NoMatchResult

@dataclass(frozen=True)
class InvariantResult:
    passed: bool
    reason: str

def gates_respected(
    result: MatchResult, *, exclusion_log: ExclusionLog | None = None,
) -> InvariantResult: ...
    # ShortlistResult: cross-reference exclusion_log vs ranked_assessments.
    # NoMatchResult: verify all candidates are in the exclusion log (trivially passes
    #   since there are no ranked_assessments to violate).

def hard_skill_not_cleared_by_adjacency(
    result: ShortlistResult,
    *,
    exclusion_log: ExclusionLog,
    scorecard: TargetProfileScorecard,
    adjacency_map: dict[str, list[str]],
) -> InvariantResult: ...
    # ShortlistResult only — meaningless on NoMatchResult (no ranked candidates).

def evidence_cited(
    result: ShortlistResult,
) -> InvariantResult: ...
    # ShortlistResult only — NoMatchResult has no assessments to check.

def no_pii_leak(
    result: MatchResult,
    *,
    seam_inputs: SeamInputs | None = None,
    known_pii: list[str] | None = None,
) -> InvariantResult: ...
    # Both types: checks exclusion_log details + near_miss names for raw PII.
    # ShortlistResult also checks ranked_assessments narratives.
    # known_pii: the raw names/emails from the original fixture (pre-pseudonymisation).

def determinism(
    run_fn: Callable[..., ShortlistResult | NoMatchResult],
    *,
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard,
    run_kwargs: dict[str, Any],
    n_trials: int = 3,
) -> InvariantResult: ...
    # ShortlistResult cases only — the test file skips this for no-match golden cases.

def adjacency_flag(
    result: ShortlistResult,
    *,
    scorecard: TargetProfileScorecard,
    adjacency_map: dict[str, list[str]],
) -> InvariantResult: ...
    # ShortlistResult only — no assessments to check on NoMatchResult.
```

**Applicability per result type:**

| Invariant | ShortlistResult | NoMatchResult |
|-----------|:-:|:-:|
| `gates_respected` | Yes | Yes (trivial) |
| `hard_skill_not_cleared_by_adjacency` | Yes | Skip |
| `evidence_cited` | Yes | Skip |
| `no_pii_leak` | Yes | Yes (exclusions + near-misses) |
| `determinism` | Yes | Skip |
| `adjacency_flag` | Yes | Skip |

**Implementation notes:**

- **`gates_respected`**: cross-reference `result.exclusion_log.exclusions` (by
  `candidate_email`) against `result.ranked_assessments[].candidate.email`. Any overlap →
  fail. When `exclusion_log` is passed explicitly, use it (for tampered-result testing);
  otherwise fall back to `result.exclusion_log`.

- **`hard_skill_not_cleared_by_adjacency`**: for each hard skill in the scorecard, find
  candidates in the exclusion log with `HARD_SKILL_MISMATCH`. Verify none of them appear
  in `ranked_assessments`. Additionally verify that the excluded candidates' skills are
  indeed only *adjacent* (not exact) per the adjacency map.

- **`evidence_cited`**: for each `CandidateAssessment`, for each `evidence.text`, check
  that `_norm(text) in _norm(candidate_source)` where `_norm` and `candidate_source` are
  the same whitespace-normalised approach as `dsm/match/score.py::_norm`/`_candidate_source`.
  Reimplement (don't import — `eval ⊥ match` is not a contract, but keeping eval free of
  match internals is good hygiene).

- **`no_pii_leak`**: takes `known_pii` (the raw names/emails from the pre-pseudonymisation
  fixture — e.g. `["Aarav", "aarav@example.com", ...]`). Asserts: (a) every
  `CandidateAssessment.candidate.email` and `.name` look like a `candidate_id` (not a raw
  name/email); (b) `narrative` and `exclusion_log` details contain no `known_pii` strings;
  (c) when `seam_inputs` is provided (captured predict/embed/rerank call args), runs
  `dsm/pii/leakscan.leak_scan` over all captured text with the `known_pii` list.
  **Structural only** — docstring states the limitation re: stub anonymiser.

- **`determinism`**: calls `run_fn` `n_trials` times with shuffled `candidates` order
  (and shuffled dict keys where applicable), then compares all `ShortlistResult`s
  byte-identical via `model_dump_json()`. The cassette LM holds the LLM fixed; this
  isolates the plumbing (Python combine, sort, tie-break).

- **`adjacency_flag`**: for each `CandidateAssessment`, compute whether adjacency credit
  was awarded to any desired skill (candidate has an adjacent skill per the map but not
  the exact one). Assert `ADJACENCY_USED in flags iff credit_awarded`.

### `dsm/eval/cases.py` (NEW)

Golden cases: each binds a seed role to cassette responses + expected outcomes.

```python
@dataclass(frozen=True)
class CassetteKey:
    case_id: str
    signature: str       # "clarify" or "score"
    prompt_hash: str     # sha256 of the DSPy Signature class source (docstring + fields)
    model_version: str   # from config models.reasoning_llm

@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    candidates: list[Candidate]
    scorecard: TargetProfileScorecard
    cassette_dir: Path               # tests/fixtures/cassettes/<case_id>/
    expected_type: Literal["shortlist", "no_match"]
    # For shortlist: expected ranked candidate_ids in order
    expected_ranked_ids: list[str] | None = None
    # For no-match: expected to produce NoMatchResult
    expected_no_match: bool = False

def load_golden_cases() -> list[GoldenCase]: ...

def load_cassette(case_id: str, signature: str) -> dict: ...

def validate_cassette_freshness(case: GoldenCase) -> None:
    """Raise if any cassette's key doesn't match current prompt/model version."""

def pseudonymise_candidates(
    candidates: list[Candidate],
) -> tuple[list[Candidate], list[str]]:
    """Mirror GoldCandidateStore: replace email/name with candidate_id.

    Returns (pseudonymised_candidates, known_pii) where known_pii is the original
    names + emails for leak-scanning. Uses dsm.pii.vault.derive_candidate_id or a
    test-stable equivalent (deterministic HMAC).
    """

class CassetteLM:
    """Produces predict callables that replay recorded clarify/score responses.

    Not a single object — produces two separate callables matching the existing seams:
      cassette.clarify_predict -> ClarifyPredictor  (Callable[[OpenRole], ScorecardClarification])
      cassette.score_predict   -> ScorePredictor     (Callable[[TargetProfileScorecard, Candidate], ScoreExtraction])

    Keyed by (case_id, signature). If a call doesn't match a recorded response,
    raises RuntimeError("stale cassette — re-record").
    """
    def __init__(self, case_id: str, cassette_dir: Path): ...
    def clarify_predict(self) -> ClarifyPredictor: ...
    def score_predict(self) -> ScorePredictor: ...
```

**Cassette format** (`tests/fixtures/cassettes/<case_id>/clarify.json`):

```json
{
  "key": {
    "case_id": "ROLE-01",
    "signature": "clarify",
    "prompt_hash": "<sha256 of RoleClarification Signature source>",
    "model_version": "openrouter/anthropic/claude-sonnet-4-6"
  },
  "response": {
    "hard_depth_skills": [...],
    "desired_skills": [...],
    "clarification_notes": "..."
  }
}
```

**Golden cases defined:**

| Case ID | Fixture | Type | Tests |
|---------|---------|------|-------|
| `ROLE-01` | `fixtures.role_01()` (enriched + extended) | shortlist | gates-respected (Aarav excluded), hard-skill-not-cleared-by-adjacency (Suresh excluded), evidence-cited, no-PII-leak, determinism, adjacency-flag (desired skill `java` → adjacency credit for candidates with adjacent skill) |
| `ROLE-02` | `fixtures.role_02()` (enriched) | shortlist | gates-respected (location), evidence-cited |
| `ROLE-03` | `fixtures.role_03()` (enriched) | no_match | gates-respected (all excluded), no-PII-leak (on `NoMatchResult`) |
| tampered | inline in test file | shortlist (bad) | deliberately-failing for each invariant |

**ROLE-01 extension (issues #3 + #4):** Add one candidate **Suresh** with `skill="java"`
(adjacent to `kotlin` via `adjacency_map`) and no `kotlin` skill → excluded by
`exact_hard_skill_filter` with `HARD_SKILL_MISMATCH`. This gives `hard_skill_not_cleared_by_adjacency`
a real exclusion to verify.

Add `desired_skills=[SkillRequirement(name="java", depth=SkillDepth.DESIRED)]` to the ROLE-01
scorecard. Karan has `kotlin` only (no `java`) → gets adjacency credit (0.5) on the desired
skill since `kotlin` is adjacent to `java` → `ADJACENCY_USED` flag fires. This gives
`adjacency_flag` a genuine exercise of the "credit awarded → flag present" direction.

**ROLE-03 invariant subset:** Since ROLE-03 returns `NoMatchResult`, only `gates_respected`
and `no_pii_leak` are run (the other four have nothing to check on a no-match result).

Should-fail cases (tampered fixtures) are built inline in the test file, not in `cases.py`
— the golden cases represent *correct* pipeline behaviour; tampered fixtures are test-only.

### `tests/fixtures/__init__.py` (ENRICH)

Add to the `_candidate` helper and the role builders:

- **`profile_summary`**: a short string (1–2 sentences) per candidate describing their
  background. Hand-authored, trusted ground truth.
- **`FeedbackSignals.entries`**: 1–2 `FeedbackEntry` items per candidate where relevant
  (e.g. ROLE-01 Karan has a positive EE feedback entry, Vivaan has a client feedback entry
  with `retention_flag=True`).
- **Source text**: the `profile_summary` + feedback `text` fields are the source corpus
  against which `evidence-cited` verifies quotes. They must contain exact phrases the
  cassette `score` response can cite.

**Constraints:**
- Keep existing gates/rank tests green — the new fields are additive (optional on `Candidate`).
- The `_candidate` helper gains optional `profile_summary` and `feedback_entries` params.
- Enrichment is small and hand-authored (a dozen lines across the three roles).

### `tests/eval/conftest.py` (NEW)

Autouse guard for `eval_offline` tests. Patches live provider entry points to raise.

```python
import pytest

@pytest.fixture(autouse=True)
def _block_live_providers(request, monkeypatch):
    """Block live providers in eval_offline tests (R-12)."""
    if "eval_offline" not in [m.name for m in request.node.iter_markers()]:
        return

    def _raise(*a, **kw):
        raise RuntimeError("live provider called in offline eval")

    # Patch ModalEmbedClient and the OpenRouter/DSPy LM constructor
    monkeypatch.setattr("dsm.index.embed_client.ModalEmbedClient.__init__", _raise)
    # Patch the DSPy LM creation path used by make_clarify_predictor / make_score_predictor
    monkeypatch.setattr("dsm.match.clarify.make_clarify_predictor", _raise)
    monkeypatch.setattr("dsm.match.score.make_score_predictor", _raise)

def _has_keys() -> bool:
    """Check if OpenRouter + Modal API keys are available."""
    import os
    return bool(os.environ.get("OPENROUTER_API_KEY")) and bool(
        os.environ.get("MODAL_TOKEN_ID")
    )
```

A dedicated test in `test_invariants.py` proves the guard fires (instantiating a real
client in an `eval_offline` test errors).

### `tests/eval/test_invariants.py` (NEW) — Tier 1

```python
@pytest.mark.eval_offline
class TestGatesRespected:
    def test_passes_on_golden_case(self, golden_role_01): ...
    def test_detects_gated_candidate_in_shortlist(self): ...

# ... one class per invariant, each with passing + deliberately-failing tests
```

- Drives `run_match` with cassette LM (temp 0) over each golden case.
- Asserts all six invariants pass on ROLE-01/02/03 + negative.
- Each invariant has a deliberately-failing fixture (tampered `ShortlistResult`).
- Deterministic, no network — runs under `make check`.

### `tests/eval/test_signatures.py` (NEW) — Tier 2

```python
@pytest.mark.eval_offline
class TestClarifySignature:
    def test_output_well_formed(self, cassette_clarify): ...

@pytest.mark.eval_offline
class TestScoreSignature:
    def test_sub_scores_in_range(self, cassette_score): ...
    def test_citation_present(self, cassette_score): ...
    def test_hard_skill_no_adjacency_credit(self, cassette_score): ...
```

Pins `clarify` and `score` DSPy signatures against fixed cassette inputs. Validates:
- Sub-scores ∈ [0,1]
- At least one citation present
- Hard skill never credited via adjacency in the raw LLM output

### `tests/eval/test_live_smoke.py` (NEW) — Tier 3

```python
@pytest.mark.eval_live
@pytest.mark.skipif(not _has_keys(), reason="No API keys for live eval")
class TestLiveSmoke:
    def test_real_llm_shortlist_well_formed(self): ...

@pytest.mark.eval_live
@pytest.mark.skipif(not _has_keys(), reason="No API keys for live eval")
class TestCassetteDriftGuard:
    def test_live_responses_match_committed_cassettes(self): ...
```

- Live smoke: one real-LLM pass over ROLE-01, asserts a well-formed `ShortlistResult`.
- Drift guard: re-records into a temp dir, diffs against committed cassettes.
- Both skip cleanly without keys (never red on key-less CI).

### `Makefile` (REPLACE stub)

```makefile
eval:
	uv run pytest tests/eval -m "eval_offline or eval_live" -v

eval-record:
	uv run python -m dsm.eval.record

check: format lint typecheck test eval-tier1 imports

test:
	uv run pytest tests/ --ignore=tests/eval -v

eval-tier1:
	uv run pytest tests/eval/test_invariants.py -v

check-all: check eval
```

**Collection logic (matches AD-096 exactly):**
- `make test` runs all unit tests, **excluding** `tests/eval/` entirely.
- `make eval-tier1` runs **only** `tests/eval/test_invariants.py` (Tier-1 invariant evals,
  `eval_offline`). This is a separate target so `make check` includes Tier-1 without
  pulling in Tier-2 signature regression.
- `make eval` runs all of `tests/eval/` (`eval_offline or eval_live`) — Tiers 1+2+3.
- `make check` = format + lint + typecheck + unit tests + Tier-1 evals + import contracts.
- `make check-all` = `make check` + `make eval` (Tier-1 re-runs; Tiers 2+3 added).

### `dsm/eval/README.md` (UPDATE)

Replace the "not configured" placeholder with the tier model + how to run.

### `pyproject.toml` (UPDATE)

```toml
[tool.pytest.ini_options]
markers = [
    "eval_offline: deterministic eval (cassette LM, no network, no keys)",
    "eval_live: live eval (needs API keys; skipif absent)",
]
```

Drop `promptfoo` from dev dependencies (per AD-095 option a).

---

## Cassette discipline

- **Location:** `tests/fixtures/cassettes/<case_id>/{clarify,score}.json`
- **Keyed by:** `(case_id, signature, prompt_hash, model_version)`
- **Regeneration:** `make eval-record` / `uv run python -m dsm.eval.record`
- **Staleness is loud:** a key mismatch → test failure ("stale cassette — re-record"),
  never a live fallback, never a skip.
- **Drift guard:** Tier-3 re-records into a temp dir and diffs against committed cassettes.

---

## Fixture enrichment plan (Deliverable 5)

No synthetic-data generator. Small, curated, hand-authored set:

**ROLE-01 enrichment (4 candidates pass gates; 1 excluded on availability; 1 excluded on hard skill):**

Scorecard change: add `desired_skills=[SkillRequirement(name="java", depth=SkillDepth.DESIRED)]`
so `adjacency_flag` is exercised (Karan has `kotlin` which is adjacent to `java` →
adjacency credit → `ADJACENCY_USED` flag).

- Karan: `profile_summary="5 years Kotlin/Android development, payments domain experience at fintech startup."`, `FeedbackEntry(source=INTERNAL_EE, text="Strong Kotlin skills, delivered payment gateway integration on time.", sentiment="positive")`
- Vivaan: `profile_summary="3 years Kotlin backend, microservices architecture."`, `FeedbackEntry(source=CLIENT, text="Good communicator, picked up Kotlin coroutines quickly.", sentiment="positive", retention_flag=True)`
- Rahul: `profile_summary="4 years Kotlin/Spring Boot, banking sector."`, `FeedbackEntry(source=INTERNAL_EE, text="Reliable delivery, solid Kotlin fundamentals.", sentiment="positive")`
- Vikram (new joiner): `profile_summary="2 years Kotlin, recent bootcamp graduate."` (no feedback — new joiner)
- Aarav (excluded on availability): `profile_summary="6 years Kotlin/JVM, senior developer."` (RollingOff +17d past deadline)
- **Suresh (NEW — excluded on hard skill):** `email="suresh@example.com"`, `name="Suresh"`,
  `city="Chennai"`, `availability=FreeNow()`, `source=BEACH`, `skill="java"` (adjacent to
  `kotlin` via `adjacency_map` but NOT `kotlin` itself). Excluded by `exact_hard_skill_filter`
  with `HARD_SKILL_MISMATCH`. Exercises `hard_skill_not_cleared_by_adjacency` invariant.
  `profile_summary="5 years Java/Spring, enterprise banking systems."`, `FeedbackEntry(source=INTERNAL_EE, text="Strong Java backend developer, reliable delivery.", sentiment="positive")`

**ROLE-02 enrichment (3 pass gates):**
- Karan: `profile_summary="4 years React/TypeScript, component library development."`, `FeedbackEntry(source=INTERNAL_EE, text="Built reusable React component library.", sentiment="positive")`
- Rahul: `profile_summary="3 years React/Next.js, e-commerce frontends."`, `FeedbackEntry(source=CLIENT, text="Delivered React migration ahead of schedule.", sentiment="positive")`
- Priya: `profile_summary="5 years React/Vue.js, design system specialist."`, `FeedbackEntry(source=INTERNAL_EE, text="Excellent React architecture skills.", sentiment="positive")`

**ROLE-03 enrichment (all excluded — no scoring, but near-miss path exercises):**
- Minimal enrichment (profile_summary only) — these candidates never reach the scoring step.

The cassette `score` responses will contain `evidence[].text` values that are exact
substrings of these profile_summary / feedback texts — so `evidence-cited` has real
quotes to verify.

---

## SeamInputs capture (for no-PII-leak)

A lightweight `SeamInputs` dataclass captures the arguments passed to `predict`
(clarify/score) and `embed`/`rerank` during a run, so the no-PII-leak evaluator can
inspect them:

```python
@dataclass
class SeamInputs:
    clarify_inputs: list[dict[str, Any]] = field(default_factory=list)
    score_inputs: list[dict[str, Any]] = field(default_factory=list)
    embed_inputs: list[str] = field(default_factory=list)
    rerank_inputs: list[tuple[str, list[str]]] = field(default_factory=list)
```

In eval mode, wrapper predictors capture their inputs into a shared `SeamInputs` instance
before delegating to the cassette LM. The `no_pii_leak` evaluator then receives both the
`SeamInputs` and the `known_pii` list (original names + emails returned by
`pseudonymise_candidates`) and runs `dsm.pii.leakscan.leak_scan` over all captured text.

---

## Out of scope

- **Narrative-faithfulness LLM judge** (G-Eval) — unvalidated judge is worthless; needs
  labelled data + TPR/TNR ≥ 80% validation. Documented as follow-on.
- **Retrieval-quality metrics** (Recall@K, contextual precision/recall) — meaningful only
  once hybrid recall flips ON (AD-089).
- **Synthetic-data generator** — golden cases are small + hand-authored.
- **Observability platform / online monitoring** — beyond POC.
- **Building the live `PseudonymisedLM` Presidio anonymiser** — separate Lane-C task.
