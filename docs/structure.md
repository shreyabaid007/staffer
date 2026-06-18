# Structure Steering — Demand–Supply Matcher

> Where things live and what may depend on what. Keeps modules honest and the agent oriented.

## Layout
```
repo/
  CLAUDE.md                 # agent constitution (auto-loaded)
  docs/
    product.md  tech.md  structure.md  decision.md  progress.md
  specs/<lane>-<seq>-<slug>/
    requirements.md  design.md  tasks.md
  dsm/
    models.py    # shared domain models (Candidate, *Scorecard, *Assessment)
    ingest/      # CSV supply + Docling resume + feedback parsers → bronze/silver/gold (land·parse·silver·enrich·merge·reconcile·index); see ee-ingestion-architecture §13
    pii/         # Presidio/spaCy; PseudonymisedLM (the ONLY path to OpenRouter); redact·leakscan·vault (encrypted identity store)
    index/       # Modal embed client; Milvus client; hybrid retrieval + rerank
    match/       # gates.py (pure), clarify.py (DSPy), score.py (DSPy), rank.py
    cli/         # Typer: ingest / match / explain
    eval/        # Promptfoo configs; DeepEval cases; invariants
  modal/         # Modal app: embedder.py (BGE function, GPU spec)
  config/        # weights, adjacency map, gate rules, model IDs, K
  data/          # raw/ inputs · bronze/ silver/ gold/ (content-addressed, immutable) · .cache/ (content+version keyed)
  tests/         # unit tests (mock all network/LLM)
  Makefile       # the harness: `make check`, `make eval`
```

## Naming conventions

- **Branches:** `<type>/<lane>/<seq>-<slug>` — e.g. `feat/c/001-gates-rank`. Types: `feat`, `fix`, `docs`, `refactor`. Lane is lowercase `a`, `b`, or `c`.
- **Spec folders:** `specs/<lane>-<seq>-<slug>/` — e.g. `specs/c-001-gates-rank/`. Lane prefix keeps specs sortable by owner.
- **Sequence numbers** are per-lane and zero-padded to three digits (`001`, `002`, …).

## Spec format (`specs/<lane>-<seq>-<slug>/`)
- **`requirements.md`** — user story + acceptance criteria in **EARS** form (*"WHEN `<trigger>`, the system SHALL `<behaviour>`"*), machine-verifiable where possible; reference the product invariants.
- **`design.md`** — module(s) touched, data contracts (Pydantic), phase(s) involved, edge cases, **the eval cases to add**.
- **`tasks.md`** — ordered, atomic, independently testable; each mapped to an acceptance criterion (one task = one commit).
- Specs are **per-feature folders**, never one growing doc. **Human sign-off on the spec before code.**

## Module contracts — the seven phases
Each phase is a module with **one typed input and one typed output**. Build and test them independently.
- `ingest` (CSV snapshots + resumes + feedback) → `dict[candidate_id, Candidate]`  *(candidates only — roles enter at query time via `match/clarify`; `candidate_id` = HMAC(email), AD-067; snapshot reconcile + tombstones, AD-070)*
- `index` (Candidates) → Milvus collection  *(capability-only embed text excludes PII; dense + `skill_set`/BM25, AD-072)*
- `match/clarify` (RawRole | text) → `TargetProfileScorecard`
- `match/gates` (Candidates, Scorecard) → `EligiblePool`, `ExclusionLog`  *(pure, LLM-free)*
- `index/retrieve` (EligiblePool, Scorecard) → top-K Candidates  *(hybrid recall → rerank, AD-071)*
- `match/score` (Scorecard, Candidate) → `CandidateAssessment`
- `match/rank` (Assessments) → `ShortlistResult`  *(NoMatchResult built by orchestrator per AD-063(c))*

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
