# Slice 0: Foundation — Requirements

## User story
As **a member of the three-engineer team**, I need **a frozen set of shared domain contracts, a running end-to-end CLI over stubs, and a green harness** so that **we can split into Data, Reasoning, and Quality lanes without contract churn blocking parallel work**.

## Acceptance criteria (EARS)

### AC-001: Domain contracts exist and are typed
**WHEN** the foundation slice is complete,  
**THEN** the system **SHALL** have a `dsm/models.py` file defining all shared domain contracts as Pydantic v2 models with full type hints:
- `Candidate`, `OpenRole`, `TargetProfileScorecard`, `EligiblePool`, `ExclusionLog`, `CandidateAssessment`, `ShortlistResult`, `NoMatchResult`
- All sub-models: `Skill`, `SkillRequirement`, `Location`, `AvailabilityState`, `FeedbackSignals`, `Flag`, `EvidenceCitation`

### AC-002: End-to-end CLI runs on stubs
**WHEN** a user invokes `uv run dsm match --role-id ROLE-01`,  
**THEN** the system **SHALL**:
- Parse the command successfully
- Load or stub the role data
- Execute all seven phases (ingest, index, clarify, gates, retrieve, score, rank) with stubbed implementations
- Return a valid `ShortlistResult` or `NoMatchResult` to stdout
- Exit with status 0

### AC-003: Harness is green
**WHEN** a developer runs `make check`,  
**THEN** the system **SHALL**:
- Format code with `ruff format` (no changes)
- Pass `ruff check` with no violations
- Pass `pyright` with no type errors
- Run `pytest` with all tests passing
- Pass `import-linter` contracts (gates.py has no LLM/PII/index imports)
- Exit with status 0

### AC-004: Import contracts are enforced
**WHEN** the import-linter runs,  
**THEN** the system **SHALL** enforce:
- `match/gates.py` imports **nothing** from `pii/`, `index/`, or LLM orchestration modules
- All external LLM access routes through `pii/PseudonymisedLM` (no direct provider imports outside that module)
- `config/` is imported, never written
- `dsm/models.py` is the single definition point for domain models (no duplicates)

### AC-005: Eval scaffold is present
**WHEN** a developer runs `make eval`,  
**THEN** the system **SHALL**:
- Locate `dsm/eval/` with placeholder Promptfoo + DeepEval configurations
- Report "eval suite not yet configured" or equivalent
- Exit with a known status (may be non-zero with clear message)
- **NOT** silently pass (per product.md: "100% pass = insufficient coverage")

### AC-006: Project is reproducible
**WHEN** a fresh checkout is cloned and `mise install && uv sync` is run,  
**THEN** the system **SHALL**:
- Install Python 3.12 via mise
- Install all locked dependencies via uv
- Complete without errors
- Allow `make check` to run successfully

## Product invariant alignment
This slice establishes the **technical foundation** for enforcing product invariants in later slices:
- **AC-004** guarantees gates are deterministic (AD-002) by preventing LLM imports
- **AC-001** embeds the hard-skill vs adjacency distinction in `SkillRequirement.depth`
- **AC-001** includes `EvidenceCitation` for future traceability (product invariant: every claim cites evidence)
- **AC-002** ensures the no-match path exists from day one (`NoMatchResult`)

## Non-goals (deferred to later slices)
- Real data ingestion from xlsx/PDF
- Vector index population
- LLM integration (clarify, score)
- Real retrieval logic
- Eval test cases (ROLE-01, ROLE-02)
- CI pipeline (local-only for now)
