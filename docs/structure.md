# Structure Steering — Demand–Supply Matcher

> Where things live and what may depend on what. Keeps modules honest and the agent oriented.

## Layout
```
repo/
  CLAUDE.md                 # agent constitution (auto-loaded)
  docs/
    product.md  tech.md  structure.md  decision.md  progress.md
  specs/<feature>/
    requirements.md  design.md  tasks.md
  dsm/
    models.py    # shared domain models (Candidate, *Scorecard, *Assessment)
    ingest/      # xlsx + Docling profile + feedback parsers; per-module schemas; cache
    pii/         # Presidio/spaCy; PseudonymisedLM  (the ONLY path to OpenRouter)
    index/       # Modal embed client; Milvus client; hybrid retrieval
    match/       # gates.py (pure), clarify.py (DSPy), score.py (DSPy), rank.py
    cli/         # Typer: ingest / match / explain
    eval/        # Promptfoo configs; DeepEval cases; invariants
  modal/         # Modal app: embedder.py (BGE function, GPU spec)
  config/        # weights, adjacency map, gate rules, model IDs, K
  data/          # input symlinks; .cache/ (content-hash)
  tests/         # unit tests (mock all network/LLM)
  Makefile       # the harness: `make check`, `make eval`
```

## Spec format (`specs/<feature>/`)
- **`requirements.md`** — user story + acceptance criteria in **EARS** form (*"WHEN `<trigger>`, the system SHALL `<behaviour>`"*), machine-verifiable where possible; reference the product invariants.
- **`design.md`** — module(s) touched, data contracts (Pydantic), phase(s) involved, edge cases, **the eval cases to add**.
- **`tasks.md`** — ordered, atomic, independently testable; each mapped to an acceptance criterion (one task = one commit).
- Specs are **per-feature folders**, never one growing doc. **Human sign-off on the spec before code.**

## Module contracts — the seven phases
Each phase is a module with **one typed input and one typed output**. Build and test them independently.
- `ingest` → `dict[email, Candidate]`, `list[OpenRole]`
- `index` (Candidates) → Milvus collection  *(embed text excludes PII)*
- `match/clarify` (RawRole | text) → `TargetProfileScorecard`
- `match/gates` (Candidates, Scorecard) → `EligiblePool`, `ExclusionLog`  *(pure, LLM-free)*
- `index/retrieve` (EligiblePool, Scorecard) → top-K Candidates
- `match/score` (Scorecard, Candidate) → `CandidateAssessment`
- `match/rank` (Assessments) → `ShortlistResult | NoMatchResult`

## Dependency rules (enforce with import-linter in CI)
- `match/gates.py` imports **nothing** from `pii/`, `index/`, or LLM code. Pure functions only.
- All OpenRouter access is through `pii/PseudonymisedLM`. **No module calls a provider directly.**
- `config/` is imported, never written by runtime code.
- Domain models live once in `dsm/models.py` and are imported everywhere. **No duplicate model definitions.**

## Conventions
- **One module = one phase = one responsibility.** If a file does two phases' jobs, split it.
- Pydantic models: shared in `dsm/models.py`; module-local in `<module>/models.py`.
- Edit the real file over creating `_v2`; delete over commenting out.
- Tests mirror module paths (`tests/match/test_gates.py`).
