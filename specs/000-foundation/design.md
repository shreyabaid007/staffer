# Slice 0: Foundation — Design

## Overview
This slice establishes the **frozen domain contract layer** (`dsm/models.py`) that all subsequent work depends on. Changes to these models after Day 1 require team agreement + a new ADR. The slice also creates the harness (`Makefile`, tooling config) and a stubbed end-to-end CLI to validate the seven-phase architecture.

## Module structure
```
dsm/
  models.py           # shared domain contracts (this design's core output)
  cli/
    __init__.py
    main.py           # Typer CLI entry point
    commands.py       # `match` command (stub)
  match/
    __init__.py
    gates.py          # stub: returns all as eligible
    clarify.py        # stub: echoes input as scorecard
    score.py          # stub: returns fixed scores
    rank.py           # stub: sorts by email, returns top-5
  ingest/
    __init__.py
    stub.py           # hardcoded test candidates + role
  index/
    __init__.py
    stub.py           # stub: returns first N candidates
  pii/
    __init__.py
    stub.py           # stub: no-op pseudonymisation
  eval/
    README.md         # "eval suite not yet configured"
modal/
  (empty for now)
config/
  default.yaml        # weights, K, adjacency_map (empty), window_days=14
tests/
  match/
    test_gates.py     # one test: all pass through
  conftest.py
Makefile
pyproject.toml
uv.lock
mise.toml
.import-linter.yaml
```

## Domain contracts — `dsm/models.py`

All models are **Pydantic v2 BaseModel** with `frozen=True` where immutable semantics are desired. Every field has an explicit type. Optional fields use `| None` with a default.

---

### Input layer

#### `Location`
Represents a geographic location.
```python
class Location(BaseModel):
    city: str
    state: str | None = None
    country: str = "India"
    remote_eligible: bool = False  # "remote-India" in the data
```

#### `Skill`
A candidate's skill with proficiency level.
```python
class ProficiencyLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"

class Skill(BaseModel):
    name: str  # normalised lowercase (e.g., "kotlin", "react")
    proficiency: ProficiencyLevel
```

#### `AvailabilityState`
One of three discriminated union variants. Use Pydantic discriminated unions (`Field(discriminator="type")`).

```python
class FreeNow(BaseModel):
    type: Literal["free_now"] = "free_now"

class RollingOff(BaseModel):
    type: Literal["rolling_off"] = "rolling_off"
    expected_date: date
    confidence: Literal["high", "medium", "low"]  # AD-022: flag, not gate

class NewJoiner(BaseModel):
    type: Literal["new_joiner"] = "new_joiner"
    join_date: date

AvailabilityState = Annotated[
    FreeNow | RollingOff | NewJoiner,
    Field(discriminator="type")
]
```

#### `FeedbackSignals`
Internal and client feedback, weighted equally per AD-031.
```python
class FeedbackSource(str, Enum):
    INTERNAL_EE = "internal_ee"
    CLIENT = "client"

class FeedbackEntry(BaseModel):
    source: FeedbackSource
    text: str
    sentiment: Literal["positive", "neutral", "negative"] | None = None
    retention_flag: bool = False  # AD-023: "keep them" → surfaces as trade-off

class FeedbackSignals(BaseModel):
    entries: list[FeedbackEntry] = Field(default_factory=list)
```

#### `Candidate`
A person from the supply sheets.
```python
class CandidateSource(str, Enum):
    BEACH = "beach"
    ROLLING_OFF = "rolling_off"
    NEW_JOINER = "new_joiner"

class Candidate(BaseModel):
    email: str  # join key (AD-012)
    name: str
    location: Location
    availability: AvailabilityState
    skills: list[Skill]
    feedback: FeedbackSignals
    source: CandidateSource
    # Enrichment fields (nullable until profiles ingested):
    profile_summary: str | None = None
    years_experience: int | None = None
```

#### `SkillRequirement`
A role's skill requirement with depth indicator.
```python
class SkillDepth(str, Enum):
    HARD = "hard"          # must match exactly; adjacency never clears (AD-033)
    DESIRED = "desired"    # soft; adjacency gives partial credit

class SkillRequirement(BaseModel):
    name: str  # normalised lowercase
    depth: SkillDepth
    min_proficiency: ProficiencyLevel | None = None
```

#### `OpenRole`
The input role (before clarification).
```python
class OpenRole(BaseModel):
    role_id: str
    title: str
    required_skills: list[SkillRequirement]  # raw from input; clarify may refine
    preferred_skills: list[str] = Field(default_factory=list)  # adjacency candidates
    location: Location
    co_location_required: bool  # AD-020: the hard gate flag
    start_date: date
    description: str | None = None  # free text for clarification
```

---

### Phase outputs

#### `TargetProfileScorecard`
Output of `match/clarify` — the LLM's structured interpretation of the role.
```python
class TargetProfileScorecard(BaseModel):
    role_id: str
    hard_depth_skills: list[SkillRequirement]  # depth=HARD; gate enforces exact match
    desired_skills: list[SkillRequirement]     # depth=DESIRED; adjacency allowed
    location: Location
    co_location_required: bool
    start_date: date
    availability_window_days: int = 14  # AD-021
    clarification_notes: str | None = None  # LLM's reasoning
```

#### `ExclusionReason`
Why a candidate was gated out.
```python
class ExclusionReason(str, Enum):
    LOCATION_MISMATCH = "location_mismatch"
    AVAILABILITY_MISMATCH = "availability_mismatch"
    # Future: MISSING_HARD_SKILL, etc.

class Exclusion(BaseModel):
    candidate_email: str
    reason: ExclusionReason
    detail: str  # human-readable specifics

class ExclusionLog(BaseModel):
    exclusions: list[Exclusion]
```

#### `EligiblePool`
Candidates that passed gates.
```python
class EligiblePool(BaseModel):
    candidates: list[Candidate]
    scorecard_id: str  # for traceability
```

#### `Flag`
Trade-offs and warnings surfaced in assessment.
```python
class FlagType(str, Enum):
    UNVERIFIED_SKILLS = "unverified_skills"      # AD-032: new joiner
    ADJACENCY_USED = "adjacency_used"            # AD-033: partial credit
    ROLL_OFF_UNCERTAIN = "roll_off_uncertain"    # AD-022: low confidence
    RETENTION_RISK = "retention_risk"            # AD-023: client wants to keep
    # Future: MISSING_PREFERRED_SKILL, etc.

class Flag(BaseModel):
    type: FlagType
    message: str
```

#### `EvidenceCitation`
Links a claim to its source (profile, feedback, sheet).
```python
class EvidenceSource(str, Enum):
    SUPPLY_SHEET = "supply_sheet"
    PROFILE_PDF = "profile_pdf"
    FEEDBACK = "feedback"

class EvidenceCitation(BaseModel):
    source: EvidenceSource
    text: str  # the verbatim snippet
    metadata: dict[str, str] = Field(default_factory=dict)  # e.g., {"page": "2"}
```

#### `CandidateAssessment`
Scored candidate with reasoning.
```python
class CandidateAssessment(BaseModel):
    candidate: Candidate
    skill_match_score: float  # 0.0–1.0
    feedback_score: float     # 0.0–1.0
    combined_score: float     # 0.7*skill + 0.3*feedback (AD-030)
    flags: list[Flag]
    evidence: list[EvidenceCitation]
    narrative: str  # 1–2 sentence explanation
    # Sub-scores for transparency:
    hard_skill_coverage: float  # fraction of hard skills matched
    desired_skill_coverage: float
```

---

### Output layer

#### `ShortlistResult`
Success case: ranked candidates.
```python
class ShortlistResult(BaseModel):
    role_id: str
    ranked_assessments: list[CandidateAssessment]  # top K, sorted by combined_score desc
    total_eligible: int  # size of pool before ranking
    exclusion_log: ExclusionLog
    config_snapshot: dict[str, Any]  # weights, K, model IDs for reproducibility
```

#### `NoMatchResult`
Failure case: no eligible candidates.
```python
class NearMiss(BaseModel):
    candidate_email: str
    name: str
    reason: str  # why they didn't qualify
    gap_summary: str  # "free 2 weeks late" or "wrong city"

class NoMatchResult(BaseModel):
    role_id: str
    reason: str  # high-level: "no candidates passed location gate"
    near_misses: list[NearMiss]  # top 3 closest
    exclusion_log: ExclusionLog
```

---

## Config schema — `config/default.yaml`

```yaml
weights:
  skill: 0.7
  feedback: 0.3

ranking:
  top_k: 5

availability:
  window_days: 14  # AD-021

adjacency_map: {}  # AD-035: seed map TBD; empty for Slice 0

models:
  reasoning_llm: "anthropic/claude-sonnet-4"
  embedder: "BAAI/bge-base-en-v1.5"

logging:
  level: "INFO"
```

Parse with `pydantic-settings` in a future slice; Slice 0 can stub or hardcode.

---

## Stub implementations

### `dsm/cli/commands.py :: match(role_id: str)`
1. Load stub role + candidates from `ingest/stub.py`
2. Call `clarify(role)` → scorecard (stub: echo as TargetProfileScorecard)
3. Call `gates.filter_candidates(candidates, scorecard)` → eligible, exclusions (stub: all pass)
4. Call `retrieve(eligible, scorecard)` → top-K (stub: first 5)
5. Call `score(candidate, scorecard)` per candidate → assessments (stub: fixed scores)
6. Call `rank(assessments)` → ShortlistResult (stub: sort by email)
7. Print result as JSON

### `match/gates.py :: filter_candidates`
```python
def filter_candidates(
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard
) -> tuple[EligiblePool, ExclusionLog]:
    # Stub: no filtering
    return EligiblePool(candidates=candidates, scorecard_id=scorecard.role_id), \
           ExclusionLog(exclusions=[])
```

**Critical:** This module must have **zero imports** from `pii/`, `index/`, DSPy, or LLM code. Enforced by import-linter.

---

## Module contracts (seven phases)

Inputs and outputs for each phase, per `docs/structure.md`:

| Phase              | Input                              | Output                               | Slice 0 stub?                |
|--------------------|------------------------------------|--------------------------------------|------------------------------|
| `ingest`           | xlsx paths                         | `dict[str, Candidate]`, `list[OpenRole]` | Yes: hardcoded test data    |
| `index`            | `list[Candidate]`                  | Milvus collection                    | Yes: no-op                  |
| `match/clarify`    | `OpenRole`                         | `TargetProfileScorecard`             | Yes: echo as scorecard      |
| `match/gates`      | `list[Candidate]`, `Scorecard`     | `EligiblePool`, `ExclusionLog`       | Yes: all pass               |
| `index/retrieve`   | `EligiblePool`, `Scorecard`        | `list[Candidate]` (top-K)            | Yes: first N                |
| `match/score`      | `Candidate`, `Scorecard`           | `CandidateAssessment`                | Yes: fixed scores           |
| `match/rank`       | `list[CandidateAssessment]`        | `ShortlistResult | NoMatchResult`    | Yes: sort by email          |

---

## Import contracts (`.import-linter.yaml`)

```yaml
modules:
  - name: dsm.match.gates
  - name: dsm.pii
  - name: dsm.index
  - name: dspy

contracts:
  - name: "Gates are LLM-free"
    type: forbidden
    source_modules:
      - dsm.match.gates
    forbidden_modules:
      - dsm.pii
      - dsm.index
      - dspy

  - name: "LLM access only via PseudonymisedLM"
    type: layers
    layers:
      - dspy
      - dsm.pii
    # (full enforcement deferred to Slice 1 when real LLM code exists)
```

---

## Eval scaffold

`dsm/eval/README.md`:
```
# Eval suite

Not yet configured. Slice 0 establishes the contracts; eval cases (ROLE-01, ROLE-02) land in Slice 1.

Run `make eval` → exit 1 with message "eval suite not configured".
```

---

## Test coverage (Slice 0)

Minimal "can it run" tests:
- `tests/match/test_gates.py::test_stub_allows_all` — stub gates return all candidates as eligible
- `tests/test_models.py::test_contracts_parseable` — every model in `dsm/models.py` can be instantiated with valid fixture data
- `tests/test_cli.py::test_match_runs_end_to_end` — `uv run dsm match --role-id STUB-01` exits 0 and returns valid JSON

Real logic tests land in Slices 1–3.

---

## Open questions / decisions needed

None — this is the deterministic foundation. Later slices will implement real logic within these contracts.
