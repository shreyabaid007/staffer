# dspy-compile — Offline MIPROv2 compilation of DSPy signatures

## User story
As the system operator, I want to compile the DSPy scoring/clarify/intake signatures against the
golden set using MIPROv2 so that optimised prompts can be A/B-compared against hand-written ones
without modifying the deterministic spine.

## Scope & framing
Add offline prompt compilation via `dspy.MIPROv2` against the golden set (AD-104). The compiled
artefacts are versioned files under `artifacts/` — never auto-deployed. Runtime supports both
the default hand-written prompts and a `--compiled` flag that loads the compiled version for A/B
comparison.

### Constraints carried over unchanged
- **Eligibility stays deterministic + LLM-free** (AD-002). Compilation optimises prompts, not gates.
- **PII boundary holds** (AD-101/102). Compilation runs on pseudonymised golden data.
- **Frozen contract untouched** (AD-060). No changes to `dsm/models.py`.
- **Determinism invariant preserved** (AD-066). Compiled artefacts are pinned versions; the
  derivation-version scheme extends to `(prompt_version, compiled_version, model_version)`.
- **Existing eval invariants untouched.** The six Tier-1 invariants remain the commit gate.

## Out of scope (this slice)
Auto-deployment of compiled artefacts (always manual promotion). Compilation of ingest enrichment
signatures (resume/feedback — these are ingest-time, not query-time). Online/continuous compilation.
Custom MIPROv2 metrics beyond the existing faithfulness + Recall@K. Training set augmentation beyond
the 21 golden cases.

## Functional requirements (EARS)

### FR-1 — Compile command
- **FR-1-AC-1:** WHEN the operator runs `dsm compile --signature <name>`, THEN MIPROv2 runs against
  the golden set and saves the compiled artefact to `artifacts/compiled_<name>_v<N>.json`.
- **FR-1-AC-2:** The metric adapter reuses the existing faithfulness judge (`dsm/eval/faithfulness.py`)
  and Recall@K (`dsm/eval/retrieval_quality.py`) from the eval harness.
- **FR-1-AC-3:** The golden set's `GoldenCase` instances are transformed into `dspy.Example` objects.
- **FR-1-AC-4:** `auto="light"` is used (21 cases is below typical MIPROv2 sample sizes of 50+).
- **FR-1-AC-5:** A stronger teacher LM (e.g. Claude Opus via OpenRouter) is used during compilation;
  the deployed model is the current `models.reasoning_llm` from config.
- **FR-1-AC-6:** The version number `<N>` auto-increments based on existing artefacts in `artifacts/`.

### FR-2 — Runtime loading
- **FR-2-AC-1:** `dsm match --compiled <path>` loads the compiled artefact via `dspy.load()` and uses
  it instead of the hand-written predictor.
- **FR-2-AC-2:** `dsm match` (without `--compiled`) uses hand-written prompts as before (no change).
- **FR-2-AC-3:** The `config_snapshot` on the result carries `compiled_version` when a compiled
  artefact is in use (so explain/eval can distinguish runs).

### FR-3 — Save/load round-trip
- **FR-3-AC-1:** Compiled artefacts use DSPy's `Module.save()` / `dspy.load()` for stable persistence.
- **FR-3-AC-2:** Artefacts are versioned files under `artifacts/` (gitignored for size; CI promotes
  known-good versions).

## Non-functional requirements

- **NF-1:** `dsm/compile.py` must not import `dsm.pii`, `dsm.ingest`, or `modal`.
- **NF-2:** Compilation is offline — it runs `make eval`-tier (key-gated, never in `make check`).
- **NF-3:** The `artifacts/` directory is gitignored; compiled artefacts are not committed.
