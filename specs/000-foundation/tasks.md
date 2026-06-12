# Slice 0: Foundation — Tasks

> **Execution rule:** One task = one commit. Tasks are ordered; complete in sequence. Stop after each task to verify `make check` is still green (once the harness exists).

---

## Task F-001: Create project structure and tooling config
**Maps to:** AC-006 (reproducibility), AC-003 (harness)

**Actions:**
1. Create `pyproject.toml` with:
   - Project metadata (name="dsm", version="0.1.0", requires-python=">=3.12")
   - Dependencies: `pydantic>=2.0`, `typer`, `pyyaml`, `python-dateutil`
   - Dev dependencies: `pytest`, `pyright`, `ruff`, `import-linter`
   - Build system: hatchling
   - Scripts: `dsm = "dsm.cli.main:app"`
2. Create `mise.toml` specifying Python 3.12
3. Create `uv.lock` by running `uv sync`
4. Create `.gitignore` (exclude `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `*.pyc`, `.venv/`, `data/.cache/`)

**Acceptance:** `mise install && uv sync` completes without error.

**Commit:** `chore(foundation): project structure and tooling config`

---

## Task F-002: Create Makefile harness
**Maps to:** AC-003 (harness is green)

**Actions:**
1. Create `Makefile` with targets:
   ```makefile
   .PHONY: check format lint typecheck test check-imports

   format:
       uv run ruff format .
   
   lint:
       uv run ruff check --fix .
   
   typecheck:
       uv run pyright dsm/ tests/
   
   test:
       uv run pytest
   
   check-imports:
       uv run import-linter
   
   check: format lint typecheck test check-imports
   
   eval:
       @echo "Eval suite not yet configured (Slice 0 foundation only)"
       @exit 1
   ```
2. Create `ruff.toml` with baseline config (line-length=100, target-version=py312)
3. Create `pyproject.toml` section for pyright (strict mode, typeCheckingMode="strict")

**Acceptance:** `make check` runs all steps (may fail tests until later tasks).

**Commit:** `chore(foundation): add Makefile harness with check target`

---

## Task F-003: Write domain contracts
**Maps to:** AC-001 (contracts exist and are typed)

**Actions:**
1. Create `dsm/models.py` with all models per `design.md`:
   - Enums: `ProficiencyLevel`, `FeedbackSource`, `CandidateSource`, `SkillDepth`, `ExclusionReason`, `FlagType`, `EvidenceSource`
   - Base models: `Location`, `Skill`, `FreeNow`, `RollingOff`, `NewJoiner`, `AvailabilityState` (discriminated union)
   - Feedback: `FeedbackEntry`, `FeedbackSignals`
   - Input: `Candidate`, `SkillRequirement`, `OpenRole`
   - Phase outputs: `TargetProfileScorecard`, `Exclusion`, `ExclusionLog`, `EligiblePool`
   - Assessment: `Flag`, `EvidenceCitation`, `CandidateAssessment`
   - Results: `NearMiss`, `ShortlistResult`, `NoMatchResult`
2. All fields explicitly typed; use Pydantic v2 syntax (`Field`, `discriminator`, etc.)
3. Add docstrings to each model (one-line purpose)

**Acceptance:** `pyright dsm/models.py` passes with no errors.

**Commit:** `feat(foundation): define frozen domain contracts in dsm/models.py per AD-060`

*(New ADR AD-060 implicitly: "Domain contracts frozen after Slice 0")*

---

## Task F-004: Write contract instantiation tests
**Maps to:** AC-001 (contracts are typed)

**Actions:**
1. Create `tests/conftest.py` with pytest fixtures for valid instances of each model
2. Create `tests/test_models.py::test_all_contracts_instantiate` — instantiate every model from `dsm/models.py` via fixtures and assert no validation errors
3. Create `tests/test_models.py::test_availability_discriminator` — verify `FreeNow | RollingOff | NewJoiner` union works

**Acceptance:** `uv run pytest tests/test_models.py -v` passes.

**Commit:** `test(foundation): verify all domain contracts instantiate`

---

## Task F-005: Stub ingest module
**Maps to:** AC-002 (CLI runs end-to-end)

**Actions:**
1. Create `dsm/ingest/__init__.py`
2. Create `dsm/ingest/stub.py` with:
   - `get_stub_candidates() -> list[Candidate]` — returns 3 hardcoded candidates (one Beach, one RollingOff, one NewJoiner) with varied skills/locations
   - `get_stub_role() -> OpenRole` — returns a single test role (ROLE-STUB-01)

**Acceptance:** `from dsm.ingest.stub import get_stub_candidates; assert len(get_stub_candidates()) == 3`

**Commit:** `feat(ingest): add stub data for end-to-end testing`

---

## Task F-006: Stub gates module (pure, no LLM)
**Maps to:** AC-002 (CLI runs), AC-004 (import contracts)

**Actions:**
1. Create `dsm/match/__init__.py`
2. Create `dsm/match/gates.py` with:
   ```python
   from dsm.models import Candidate, TargetProfileScorecard, EligiblePool, ExclusionLog
   
   def filter_candidates(
       candidates: list[Candidate],
       scorecard: TargetProfileScorecard
   ) -> tuple[EligiblePool, ExclusionLog]:
       """Stub: all candidates pass."""
       return (
           EligiblePool(candidates=candidates, scorecard_id=scorecard.role_id),
           ExclusionLog(exclusions=[])
       )
   ```
3. **CRITICAL:** Verify no imports from `pii/`, `index/`, or any LLM library

**Acceptance:** `pyright dsm/match/gates.py` passes; module imports only `dsm.models`.

**Commit:** `feat(match): add stub gates module (pure, LLM-free per AD-002)`

---

## Task F-007: Stub clarify, score, rank modules
**Maps to:** AC-002 (CLI runs end-to-end)

**Actions:**
1. Create `dsm/match/clarify.py`:
   ```python
   from dsm.models import OpenRole, TargetProfileScorecard
   
   def clarify_role(role: OpenRole) -> TargetProfileScorecard:
       """Stub: echo role as scorecard."""
       return TargetProfileScorecard(
           role_id=role.role_id,
           hard_depth_skills=[s for s in role.required_skills if s.depth.value == "hard"],
           desired_skills=[s for s in role.required_skills if s.depth.value == "desired"],
           location=role.location,
           co_location_required=role.co_location_required,
           start_date=role.start_date,
           availability_window_days=14
       )
   ```

2. Create `dsm/match/score.py`:
   ```python
   from dsm.models import Candidate, TargetProfileScorecard, CandidateAssessment
   
   def score_candidate(
       candidate: Candidate,
       scorecard: TargetProfileScorecard
   ) -> CandidateAssessment:
       """Stub: fixed scores."""
       return CandidateAssessment(
           candidate=candidate,
           skill_match_score=0.75,
           feedback_score=0.6,
           combined_score=0.7 * 0.75 + 0.3 * 0.6,  # 0.705
           flags=[],
           evidence=[],
           narrative=f"Stub assessment for {candidate.name}",
           hard_skill_coverage=0.8,
           desired_skill_coverage=0.7
       )
   ```

3. Create `dsm/match/rank.py`:
   ```python
   from dsm.models import CandidateAssessment, ShortlistResult, ExclusionLog
   
   def rank_assessments(
       assessments: list[CandidateAssessment],
       role_id: str,
       exclusion_log: ExclusionLog,
       top_k: int = 5
   ) -> ShortlistResult:
       """Stub: sort by combined_score desc, take top K."""
       ranked = sorted(assessments, key=lambda a: a.combined_score, reverse=True)[:top_k]
       return ShortlistResult(
           role_id=role_id,
           ranked_assessments=ranked,
           total_eligible=len(assessments),
           exclusion_log=exclusion_log,
           config_snapshot={"top_k": top_k, "weights": {"skill": 0.7, "feedback": 0.3}}
       )
   ```

**Acceptance:** `pyright dsm/match/*.py` passes.

**Commit:** `feat(match): add stub clarify, score, rank modules`

---

## Task F-008: Stub index and pii modules
**Maps to:** AC-002 (CLI runs end-to-end)

**Actions:**
1. Create `dsm/index/__init__.py` and `dsm/index/stub.py`:
   ```python
   from dsm.models import EligiblePool, TargetProfileScorecard, Candidate
   
   def retrieve_candidates(
       pool: EligiblePool,
       scorecard: TargetProfileScorecard,
       top_k: int = 10
   ) -> list[Candidate]:
       """Stub: return first N candidates."""
       return pool.candidates[:top_k]
   ```

2. Create `dsm/pii/__init__.py` and `dsm/pii/stub.py`:
   ```python
   # Placeholder for future PseudonymisedLM
   # Slice 0: no LLM calls, so no pseudonymisation needed
   ```

**Acceptance:** Modules importable.

**Commit:** `feat(index,pii): add stub modules for retrieval and PII`

---

## Task F-009: Create CLI with match command
**Maps to:** AC-002 (end-to-end CLI runs)

**Actions:**
1. Create `dsm/cli/__init__.py`
2. Create `dsm/cli/main.py`:
   ```python
   import typer
   from dsm.cli.commands import match
   
   app = typer.Typer()
   app.command()(match)
   
   if __name__ == "__main__":
       app()
   ```

3. Create `dsm/cli/commands.py`:
   ```python
   import typer
   from dsm.ingest.stub import get_stub_candidates, get_stub_role
   from dsm.match.clarify import clarify_role
   from dsm.match.gates import filter_candidates
   from dsm.index.stub import retrieve_candidates
   from dsm.match.score import score_candidate
   from dsm.match.rank import rank_assessments
   
   def match(role_id: str = typer.Option("ROLE-STUB-01", "--role-id")):
       """Match candidates to a role (Slice 0: stubbed end-to-end)."""
       role = get_stub_role()
       candidates = get_stub_candidates()
       
       scorecard = clarify_role(role)
       eligible_pool, exclusion_log = filter_candidates(candidates, scorecard)
       retrieved = retrieve_candidates(eligible_pool, scorecard, top_k=10)
       assessments = [score_candidate(c, scorecard) for c in retrieved]
       result = rank_assessments(assessments, role.role_id, exclusion_log, top_k=5)
       
       typer.echo(result.model_dump_json(indent=2))
   ```

4. Update `pyproject.toml` scripts section to include `dsm = "dsm.cli.main:app"`

**Acceptance:** `uv run dsm match --role-id ROLE-01` exits 0 and prints valid JSON.

**Commit:** `feat(cli): add match command with stubbed end-to-end flow`

---

## Task F-010: Create config file
**Maps to:** AC-002 (CLI uses config)

**Actions:**
1. Create `config/default.yaml` per `design.md`:
   ```yaml
   weights:
     skill: 0.7
     feedback: 0.3
   
   ranking:
     top_k: 5
   
   availability:
     window_days: 14
   
   adjacency_map: {}
   
   models:
     reasoning_llm: "anthropic/claude-sonnet-4"
     embedder: "BAAI/bge-base-en-v1.5"
   
   logging:
     level: "INFO"
   ```

**Acceptance:** File exists; defer parsing to later slice.

**Commit:** `feat(config): add default configuration file`

---

## Task F-011: Create import-linter config
**Maps to:** AC-004 (import contracts enforced)

**Actions:**
1. Create `.import-linter.yaml`:
   ```yaml
   modules:
     - name: dsm.match.gates
     - name: dsm.pii
     - name: dsm.index
   
   contracts:
     - name: "Gates are LLM-free"
       type: forbidden
       source_modules:
         - dsm.match.gates
       forbidden_modules:
         - dsm.pii
         - dsm.index
   ```

2. Add `import-linter` to dev dependencies in `pyproject.toml` if not already present
3. Verify `make check-imports` runs and passes

**Acceptance:** `uv run import-linter` exits 0.

**Commit:** `chore(foundation): enforce import contracts with import-linter per AD-002`

---

## Task F-012: Create eval scaffold
**Maps to:** AC-005 (eval scaffold present)

**Actions:**
1. Create `dsm/eval/__init__.py`
2. Create `dsm/eval/README.md`:
   ```markdown
   # Eval suite
   
   Not yet configured. Slice 0 establishes the contracts; eval cases (ROLE-01, ROLE-02) 
   and invariants (gates-respected, no-PII-leak, determinism) land in Slice 1+.
   
   Run `make eval` → exit 1 with message "eval suite not configured".
   ```

3. Verify `make eval` target exists (created in F-002) and produces expected message

**Acceptance:** `make eval` exits non-zero with clear message.

**Commit:** `chore(eval): add eval scaffold placeholder`

---

## Task F-013: Write unit tests for gates
**Maps to:** AC-003 (harness is green)

**Actions:**
1. Create `tests/match/__init__.py`
2. Create `tests/match/test_gates.py`:
   ```python
   from dsm.match.gates import filter_candidates
   from dsm.models import Candidate, TargetProfileScorecard
   # Use fixtures from conftest.py
   
   def test_stub_allows_all(sample_candidates, sample_scorecard):
       """Slice 0 stub: all candidates pass gates."""
       pool, log = filter_candidates(sample_candidates, sample_scorecard)
       assert len(pool.candidates) == len(sample_candidates)
       assert len(log.exclusions) == 0
   ```

3. Extend `tests/conftest.py` with `sample_candidates` and `sample_scorecard` fixtures

**Acceptance:** `uv run pytest tests/match/test_gates.py -v` passes.

**Commit:** `test(match): add unit test for stub gates`

---

## Task F-014: Write end-to-end CLI test
**Maps to:** AC-002 (CLI runs), AC-003 (harness green)

**Actions:**
1. Create `tests/test_cli.py`:
   ```python
   import subprocess
   import json
   
   def test_match_command_runs_end_to_end():
       """Verify `dsm match` exits 0 and returns valid JSON."""
       result = subprocess.run(
           ["uv", "run", "dsm", "match", "--role-id", "ROLE-STUB-01"],
           capture_output=True,
           text=True
       )
       assert result.returncode == 0
       output = json.loads(result.stdout)
       assert "role_id" in output
       assert "ranked_assessments" in output
   ```

**Acceptance:** `uv run pytest tests/test_cli.py -v` passes.

**Commit:** `test(cli): add end-to-end test for match command`

---

## Task F-015: Verify full harness is green
**Maps to:** AC-003 (harness is green)

**Actions:**
1. Run `make check` and verify all steps pass:
   - `make format` → no changes
   - `make lint` → no violations
   - `make typecheck` → no errors
   - `make test` → all tests pass
   - `make check-imports` → no violations
2. Fix any issues found
3. Verify `mise install && uv sync` still works (AC-006)

**Acceptance:** `make check` exits 0.

**Commit:** `chore(foundation): verify full harness is green`

---

## Task F-016: Update progress.md
**Maps to:** Session handoff discipline

**Actions:**
1. Update `docs/progress.md`:
   - Current status: "Build phase: Slice 0 complete; contracts frozen"
   - Works end-to-end: "`dsm match --role-id ROLE-STUB-01` runs on stubs; `make check` green"
   - Next up: "Slice 1 — implement real gates.py (location + availability)"
   - Append session log entry with today's date
2. Append to `docs/decision.md`:
   - **AD-060 · Domain contracts frozen** — Accepted — The models in `dsm/models.py` are locked after Slice 0; changes require team agreement + new ADR. Why: prevent parallel work churn across Data / Reasoning / Quality lanes.

**Acceptance:** Files updated.

**Commit:** `docs: update progress.md and decision.md after Slice 0 completion`

---

## Summary

**16 tasks** → 16 commits. Sequential execution. Each task independently verifiable. After F-015, all acceptance criteria (AC-001 through AC-006) are met, and the foundation is ready for team split into lanes.
