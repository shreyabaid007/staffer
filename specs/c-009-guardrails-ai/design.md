# c-009 — Design

## Modules touched / added
| Path | Change |
|---|---|
| `dsm/guardrails/__init__.py` | **new** — package marker. |
| `dsm/guardrails/input_guard.py` | **new** — input validation: jailbreak + injection detection. |
| `dsm/guardrails/output_guard.py` | **new** — output validation: score bounds + narrative grounding. |
| `dsm/guardrails/narrative_guard.py` | **new** — narrative validation: bias + toxicity screening. |
| `dsm/pii/pseudonymised_lm.py` | **edit** — add pre-call input guard hook and post-call output guard hook. |
| `dsm/cli/commands.py` | **edit** — wire guardrails config into the LM builders. |
| `config/default.yaml` | **edit** — add `guardrails:` section. |
| `pyproject.toml` | **edit** — add `guardrails-ai` dependency. |
| `docs/{tech,decision}.md` | **edit** — Stack line + ADR (AD-XXX). |
| `tests/guardrails/` | **new** — unit tests for all three guard modules. |

No change to `dsm/models.py` → no `make contract-snapshot`. No change to `dsm/match/gates.py`,
`dsm/match/rank.py`, or `dsm/match/score.py` (scoring logic untouched — guardrails wrap the LM,
not the scorer).

## Architecture: where guardrails sit

```
  candidate text (resume / feedback / skills)
         │
         ▼
  ┌─────────────────────┐
  │   INPUT GUARD        │  ← dsm/guardrails/input_guard.py
  │   detect_jailbreak   │     Runs BEFORE PII redaction.
  │   on_fail=EXCEPTION  │     Rejects adversarial text.
  └──────────┬──────────┘
             │ (clean text)
             ▼
  ┌─────────────────────┐
  │  PseudonymisedLM     │  ← existing PII boundary (unchanged)
  │  redact → leak-scan  │
  │  → LLM → de-anon     │
  └──────────┬──────────┘
             │ (LLM response)
             ▼
  ┌─────────────────────┐
  │   OUTPUT GUARD       │  ← dsm/guardrails/output_guard.py
  │   valid_range        │     Score bounds [0.0, 1.0].
  │   bespoke_minicheck  │     Narrative grounding vs source.
  │   on_fail=FIX/FILTER │
  └──────────┬──────────┘
             │
             ▼
  ┌─────────────────────┐
  │  NARRATIVE GUARD     │  ← dsm/guardrails/narrative_guard.py
  │   bias_check         │     Bias detection.
  │   toxic_language     │     Toxicity detection.
  │   on_fail=REASK      │     Re-generate on failure.
  └──────────┬──────────┘
             │ (validated output)
             ▼
       score.py / enrich.py continues
```

## Why separate from PseudonymisedLM

The guardrails layer is a **composition-root concern**, not a PII concern. `PseudonymisedLM` owns
the PII boundary (redact / leak-scan / de-anon). Guardrails own content safety (injection / bias /
grounding). Mixing them would violate single responsibility and make the PII boundary harder to
reason about. Instead, guardrails are wired at the call site (like the PII `pii_context` wiring) —
the `_build_*` builders in `commands.py` compose them.

## Integration approach: guard-wrapped predictor

Rather than modifying `PseudonymisedLM` directly, we create a **guard-wrapped predictor** pattern.
Each `_build_*_predictor` function in `commands.py` already builds the LM and returns a predictor.
We add a thin wrapper that runs the input guard before calling the predictor and the output guard
after:

```python
def _guarded_score_predictor(
    base: ScorePredictor,
    config: dict,
    source_context_fn: Callable,  # returns candidate source text for grounding
) -> ScorePredictor:
    input_guard = build_input_guard(config)
    output_guard = build_output_guard(config)
    narrative_guard = build_narrative_guard(config)

    def predict(scorecard, candidate):
        # Phase 1: validate candidate text
        input_guard.validate(candidate_text(candidate))

        # Core prediction (PseudonymisedLM handles PII)
        extraction = base(scorecard, candidate)

        # Phase 2: validate scores + ground narrative
        output_guard.validate_scores(extraction.skill_match, extraction.feedback_score)
        extraction.narrative = output_guard.ground_narrative(
            extraction.narrative, source_context_fn(candidate)
        )

        # Phase 3: screen narrative
        extraction.narrative = narrative_guard.validate(extraction.narrative)

        return extraction

    return predict
```

## Module contracts

### `dsm/guardrails/input_guard.py`

```python
from guardrails import Guard, OnFailAction
from guardrails.hub import DetectJailbreak

def build_input_guard(config: dict) -> Guard | None:
    """Build the input guard from config. Returns None if disabled."""

def validate_input(guard: Guard | None, text: str, candidate_id: str) -> None:
    """Validate text. Raises InputRejectedError if injection detected."""

class InputRejectedError(Exception):
    """Raised when input text fails the injection guard. Carries candidate_id, never text."""
```

- `on_fail=OnFailAction.EXCEPTION` — adversarial input is never partially processed.
- Logs `guardrails.input_rejected` with `candidate_id` (never the text content — PII safe).
- The caller (`score.py` via the wrapped predictor, `enrich.py` via the wrapped predictor) catches
  `InputRejectedError` and handles it as an exclusion / skip.

### `dsm/guardrails/output_guard.py`

```python
from guardrails import Guard, OnFailAction
from guardrails.hub import ValidRange

def build_score_guard(config: dict) -> Guard | None:
    """Deterministic score bounds validator. Returns None if disabled."""

def validate_scores(guard: Guard | None, skill: float, feedback: float) -> tuple[float, float]:
    """Clamp scores to [0.0, 1.0]. Logs corrections. Returns (clamped_skill, clamped_feedback)."""

def build_grounding_guard(config: dict) -> Guard | None:
    """Narrative grounding validator (bespoke_minicheck). Returns None if disabled."""

def ground_narrative(guard: Guard | None, narrative: str, sources: list[str]) -> str:
    """Filter ungrounded sentences from narrative. Returns cleaned narrative."""
```

- Score bounds: `on_fail=OnFailAction.FIX` — clamp to `[0.0, 1.0]`, log correction.
- Grounding: `on_fail=OnFailAction.FILTER` — strip ungrounded sentences, log what was removed.
- Grounding runs with `metadata={"sources": sources}` where sources is the candidate's skills +
  feedback + profile_summary concatenated.

### `dsm/guardrails/narrative_guard.py`

```python
from guardrails import Guard, OnFailAction
from guardrails.hub import BiasCheck, ToxicLanguage

def build_narrative_guard(config: dict) -> Guard | None:
    """Bias + toxicity guard for narrative text. Returns None if disabled."""

def validate_narrative(guard: Guard | None, narrative: str) -> str:
    """Screen for bias/toxicity. Returns narrative (may be re-generated via reask)."""
```

- `on_fail=OnFailAction.REASK` — the LLM re-generates with the constraint.
- Reask requires passing the LLM callable to the guard. For MVP, if reask is too complex to wire
  through DSPy, fall back to `OnFailAction.EXCEPTION` and log — the operator sees the failure
  and the candidate gets a `NARRATIVE_FLAGGED` warning instead of a narrative.
- Both bias and toxicity run as a composed guard (`.use()` chain).

## Config schema (`config/default.yaml`)

```yaml
guardrails:
  # Master toggle — when false, all guardrails are bypassed (zero overhead).
  enabled: true

  input:
    # Jailbreak / prompt injection detection on candidate text.
    jailbreak_detection:
      enabled: true

  output:
    # Score bounds enforcement [0.0, 1.0].
    score_bounds:
      enabled: true

    # Narrative grounding vs candidate source context (bespoke_minicheck).
    # Uses an ML model — adds latency. Disable for fast iteration.
    grounding:
      enabled: true

  narrative:
    # Bias detection (age, gender, ethnicity) in generated narratives.
    bias_check:
      enabled: true
      # Toxicity detection in generated narratives.
    toxicity:
      enabled: true
      threshold: 0.5
```

## On-fail strategy rationale

| Guard | on_fail | Why |
|-------|---------|-----|
| `detect_jailbreak` | `EXCEPTION` | Adversarial input must be rejected, never negotiated. |
| `valid_range` | `FIX` | Clamp is deterministic and preserves the overall result. |
| `bespoke_minicheck` | `FILTER` | Remove ungrounded sentences; preserve grounded content. |
| `bias_check` | `REASK` (or `EXCEPTION` fallback) | The LLM should regenerate without bias. |
| `toxic_language` | `REASK` (or `EXCEPTION` fallback) | Same as bias. |

## Import boundary

`dsm/guardrails/` is a **new package** at the same level as `dsm/pii/` and `dsm/web/`. It:
- **Imports:** `guardrails` (external), `structlog` (logging), `dsm.config` (load config).
- **Does NOT import:** `dsm.match`, `dsm.pii`, `dsm.ingest`, `dsm.models`.
- **Is NOT imported by:** `dsm.match` — guardrails are wired at the composition root (`commands.py`),
  mirroring the PII boundary pattern where `dsm.match ⊥ dsm.pii`.

An AST import-boundary test (`tests/guardrails/test_imports.py`) enforces:
- `dsm.guardrails` does not import `dsm.match`, `dsm.pii`, `dsm.ingest`.
- `dsm.match` does not import `dsm.guardrails`.

## Testing strategy

| Test | Tier | What it checks |
|------|------|----------------|
| `test_input_guard.py` — planted injection → `InputRejectedError` | `make check` (deterministic) | FR-1-AC-2 |
| `test_input_guard.py` — clean text → passes | `make check` | FR-1 happy path |
| `test_input_guard.py` — guard disabled → no-op | `make check` | NF-2 config toggle |
| `test_output_guard.py` — score 1.5 → clamped to 1.0 | `make check` (deterministic) | FR-2-AC-2 |
| `test_output_guard.py` — score 0.7 → unchanged | `make check` | FR-2 happy path |
| `test_output_guard.py` — grounding strips ungrounded sentence | `make eval` (ML model) | FR-3-AC-2 |
| `test_narrative_guard.py` — biased text → flagged/reask | `make eval` (ML model) | FR-4-AC-2 |
| `test_narrative_guard.py` — clean narrative → passes | `make eval` | FR-4 happy path |
| `test_imports.py` — AST boundary check | `make check` | NF-1 |

## Decisions to ratify (AD-XXX placeholder)

- **AD-XXX · Guardrails AI input/output validation layer (defence-in-depth)** — Accepted — Add the
  `guardrails-ai` framework + five hub validators as a composable input/output validation layer
  around the existing `PseudonymisedLM` call sites. **Does NOT replace** the PII boundary
  (AD-101/102), deterministic gates (AD-002), citation verification (AD-073), or any existing eval
  invariant. Addresses four failure modes not covered by existing layers: (1) indirect prompt
  injection via resume/feedback text, (2) hallucinated narrative prose beyond cited quotes, (3)
  biased/discriminatory language in staffing narratives, (4) sub-score boundary violations.
  **Wired at the composition root** (`commands.py`), mirroring the PII pattern — `dsm.match` does
  not import `dsm.guardrails`. Config-driven (`guardrails:` section in `default.yaml`) with
  per-validator enable flags. Deps: `guardrails-ai` + hub validators `detect_jailbreak`,
  `valid_range`, `bespoke_minicheck`, `bias_check`, `toxic_language`. Rejected: replacing the PII
  boundary with Guardrails AI's `detect_pii` (less control, different purpose); gating `make check`
  on ML-based validators (non-deterministic); modifying `dsm/match/score.py` directly (guardrails
  wrap the predictor, not the scorer). See `specs/c-009-guardrails-ai/`.
