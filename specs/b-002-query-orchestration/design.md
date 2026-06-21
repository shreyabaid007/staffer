# B-002 Query Orchestration — Design

> **Lane:** B · **Slice:** B-2
> **Architecture ref:** `ee-query-architecture.md` §4, §6.0–6.10, §9, §10, §12, §13
> **Prerequisite:** B-1 merged (PR #18): demand parse, freshness guard, gates (AD-086), exact hard-skill filter (AD-088), rank

---

## 1. Data-flow diagram

```
Open Roles CSV ─────────────────────────────────────────────────────────────
      │
      ▼
┌─────────────────────────────┐
│ 1  Parse demand (B-1)       │  dsm/match/demand.py
│    banner → demand_as_of    │  → DemandParseOutcome
│    --role-id selects one    │
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│ 2  Clarify                  │  dsm/match/clarify.py     ◄── UPDATE
│    description → LLM path   │  → TargetProfileScorecard
│    empty → echo (stub)      │    (no redaction — §7)
│    LLM fail → echo + warn   │
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│    Load candidates          │  dsm/cli/commands.py      ◄── GoldCandidateStore (AD-091)
│    CandidateStore.get(*)    │  → list[Candidate]
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│ 3  Freshness guard (B-1)    │  dsm/match/freshness.py
│    REFUSE → block run       │  → FreshnessVerdict
└─────────┬───────────────────┘
          │ ok / warn
          ▼
┌─────────────────────────────┐
│ 4  Gate pre-filter (B-1)    │  dsm/match/gates.py
│    location + availability  │  → (EligiblePool, ExclusionLog)
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│ 5  Exact hard-skill (B-1)   │  dsm/index/retrieve.py
│    skill_set ∩ + prof floor │  → (filtered pool, exclusions)
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│ 6  Hybrid recall (OFF)      │  dsm/index/retrieve.py    ◄── ADD
│    dense + BM25 + RRF       │  → list[RetrievedCandidate]
│    (passthrough when OFF)   │    (scores None when OFF)
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│ 7  Rerank                   │  dsm/index/retrieve.py    ◄── ADD
│    cross-encoder scores     │  → list[RetrievedCandidate]
│    truncate to rerank top_k │    (truncated)
│    error → pass unranked    │
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│ 8  Score + combine          │  dsm/match/score.py       ◄── REWRITE
│    DSPy sub-scores (cited)  │  → CandidateAssessment
│    Python 0.7·s + 0.3·f    │    (flags, evidence, narrative)
│    adjacency partial credit │
└─────────┬───────────────────┘
          │
          ▼
┌─────────────────────────────┐
│ 9  Rank (B-1, KEEP)         │  dsm/match/rank.py
│    combined_score desc      │  → ShortlistResult
│    → coverages → email      │  / NoMatchResult
│    top_k truncation         │
└─────────────────────────────┘
```

---

## 2. ADRs to ratify (`T-000-ADR` — gate task)

### AD-089 — Defer hybrid recall behind `index.recall.enabled`

**Status:** Proposed. Touches no frozen model; new config keys.

**Decision:** Ship deterministic exhaustive structured scoring + full-pool
cross-encoder rerank over the gated, exact-filtered pool. Keep the dense + BM25 +
RRF recall stage fully implemented but `index.recall.enabled = false`, flipping ON
when the post-filter pool routinely exceeds ~150 candidates. Ingest-time embedding
(`dense_vector`) is produced regardless (AD-074); only query-time consumption is
deferred.

**Why:** At single-digit gated pools recall has nothing to narrow, and exhaustive
is more explainable. Reinforces AD-071; does not change AD-030 weights.

### AD-090 — Seniority is a soft signal, never a gate

**Status:** Proposed. Touches no frozen model.

**Decision:** Role seniority (Title + hard-skill `min_proficiency`) maps to a target
`Grade` used only in scoring/narrative, sourced from `CandidateIndexRecord.grade` +
`years_experience`. **No** `Candidate` amendment; **no** seniority gate. Product
invariants enumerate only location + availability as hard gates (AD-002); a seniority
gate would be unsanctioned.

### AD-091 — `CandidateStore` port + `Grade`/index models made ingest-free + `match/index ⊥ ingest` contract

**Status:** Resolved, pending sign-off. Frozen/shared-contract touch.

**Decision:** Three coupled moves:

1. **`CandidateStore` port (§6.0):** Define a `CandidateStore` protocol in
   `dsm/models.py` (`get(candidate_ids: list[str]) -> list[Candidate]`).
   `dsm/match` + `dsm/index` depend on the interface only. Implement a
   `GoldCandidateStore` adapter in `dsm/cli/` (the composition root — the only
   layer that may import `dsm/ingest/`) that reads gold via `goldstore`, hydrates
   serving `Candidate` (proficiency, feedback, profile_summary, location,
   availability). Name/email excluded from anything sent to a provider — carry a
   pseudonymised id through the pipeline, fetch identity from the vault only at
   final human-facing rendering.

2. **Make index models ingest-free:** Move `Grade` enum to `dsm/models.py` (shared).
   Keep `CandidateIndexRecord` + `RetrievedCandidate` as pure data models with
   **no `dsm.ingest` import**. Relocate the gold→record projection helpers
   (`project_filter_fields`/`build_record`/`is_indexable`, which need
   `GoldCandidate`) to a **build module** (`dsm/index/build.py`) run by `dsm index`
   — a build/composition edge exempt like the CLI orchestrator.

3. **Add the import contract:** `dsm.match, dsm.index ⊥ dsm.ingest` in
   `pyproject.toml`, with `dsm.cli` and the index-build edge (`dsm.index.build`)
   exempt. (Note: `dsm.index.build` needs `dsm.ingest.models.GoldCandidate` for the
   projection — same pattern as the CLI composition root.)

---

## 3. Modules touched

### 3.1 `dsm/models.py` — ADD (`CandidateStore` protocol, `Grade` enum)

```python
from typing import Protocol

class Grade(StrEnum):
    """Consultant grade (moved from dsm.ingest.models — shared across ingest + index + match)."""
    SENIOR_CONSULTANT = "senior_consultant"
    LEAD_CONSULTANT = "lead_consultant"
    PRINCIPAL_CONSULTANT = "principal_consultant"

class CandidateStore(Protocol):
    """Port for loading serving Candidates — the pipeline depends on the interface only (AD-091)."""
    def get(self, candidate_ids: list[str]) -> list[Candidate]: ...
```

### 3.2 `dsm/match/clarify.py` — UPDATE (add bounded LLM path)

```python
def clarify_role(role: OpenRole, *, lm: dspy.LM | None = None) -> TargetProfileScorecard:
    """Clarify an OpenRole into a TargetProfileScorecard.

    When description is empty → deterministic echo (existing stub).
    When description is non-empty and lm is provided → bounded DSPy signature.
    LLM failure → echo + logged warning.
    """
```

**DSPy signature shape (illustrative):**

```python
class ClarifySignature(dspy.Signature):
    """Refine a role's skill requirements from free-text constraints."""
    role_title: str = dspy.InputField()
    required_skills: str = dspy.InputField()
    description: str = dspy.InputField()
    hard_skills: str = dspy.OutputField(desc="semicolon-separated hard skill names with proficiency")
    desired_skills: str = dspy.OutputField(desc="semicolon-separated desired skill names")
    clarification_notes: str = dspy.OutputField(desc="1-2 sentence summary of constraints")
```

**Import boundary:** reads `role.description` directly (no redaction — §7). All LLM
calls via `PseudonymisedLM`. Stays in `dsm/match/`.

### 3.3 `dsm/index/retrieve.py` — ADD (recall + rerank)

**Already has:** `exact_hard_skill_filter` (B-1).

**Add:**

```python
def hybrid_recall(
    pool: EligiblePool,
    role_query: str,
    store: MilvusIndexStore,
    embed_client: EmbedClient,
    *,
    top_n: int = 100,
    enabled: bool = False,
) -> list[RetrievedCandidate]:
    """Hybrid dense + BM25 + RRF recall (AD-089).

    When enabled=False (default), passthrough: return all pool candidates
    as RetrievedCandidate(dense_score=None, bm25_score=None, rrf_score=None).
    When enabled=True, embed role query, run dense + BM25 + RRF fusion.
    EmbedError → fallback to passthrough + log warning.
    """

def rerank(
    role_query: str,
    candidates: list[RetrievedCandidate],
    embed_texts: dict[str, str],
    embed_client: EmbedClient,
    *,
    top_k: int = 10,
) -> list[RetrievedCandidate]:
    """Cross-encoder rerank via EmbedClient.rerank() (AD-071).

    Scores each (role_query, candidate_embed_text) pair jointly.
    Truncates to top_k. EmbedError → pass through unranked (rerank_score=None).
    """
```

### 3.4 `dsm/index/models.py` — ADD (`RetrievedCandidate`); REFACTOR (ingest-free)

```python
class RetrievedCandidate(BaseModel, frozen=True):
    """A candidate surviving recall/rerank, carrying provenance scores."""
    candidate_id: str
    dense_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
```

**Refactor:** Remove the `from dsm.ingest.models import GoldCandidate, Grade` import.
`Grade` moves to `dsm/models.py` (shared). `CandidateIndexRecord` keeps `grade: Grade`
but imports from the shared location. The gold→record projection helpers
(`project_filter_fields`, `build_record`, `is_indexable`) move to a new
`dsm/index/build.py` module (the build edge, exempt from the import contract).

### 3.5 `dsm/index/build.py` — NEW (extracted from `dsm/index/models.py`)

```python
"""Gold→index-record projection helpers (build edge — may import dsm.ingest).

This module runs at write time (`dsm index`), not at query time. The import contract
(dsm.index ⊥ dsm.ingest) exempts it because it is the build/composition edge, analogous
to the CLI orchestrator.
"""
from dsm.ingest.models import GoldCandidate
from dsm.index.models import CandidateIndexRecord, FilterFields

def is_indexable(gold: GoldCandidate) -> bool: ...
def project_filter_fields(gold: GoldCandidate) -> FilterFields: ...
def build_record(gold: GoldCandidate, *, embed_text: str, dense_vector: list[float],
                 skill_set: list[str], model_version: str) -> CandidateIndexRecord: ...
```

### 3.6 `dsm/index/text_builder.py` — ADD (`build_role_query_passage`)

```python
def build_role_query_passage(scorecard: TargetProfileScorecard) -> str:
    """Build the role-side query passage for rerank (symmetric to candidate passage).

    Composition: skills (hard+desired names) + min_proficiency-derived seniority +
    clarification_notes. Capability-only, PII-free.
    """
```

**Refactor:** `build_embed_text` and `build_skill_set` currently import
`GoldCandidate` from `dsm.ingest.models`. These move to `dsm/index/build.py`
(the build edge), so `text_builder.py` stays ingest-free.

Wait — `build_role_query_passage` reads from `TargetProfileScorecard` (from
`dsm.models`), so it has no ingest dependency. But `build_embed_text` and
`build_skill_set` use `GoldCandidate` → they **also** move to `dsm/index/build.py`.
`text_builder.py` is renamed/repurposed or emptied; the role-side builder lives
alongside the candidate builder in `build.py`, or stays as a query-time module that
imports only `dsm.models`.

**Decision:** Keep `dsm/index/text_builder.py` for the **query-time** role passage
builder only (`build_role_query_passage`). The **write-time** `build_embed_text` +
`build_skill_set` + `included_skills` move to `dsm/index/build.py`. This clean split
means `text_builder.py` has no ingest import, and `build.py` has both the ingest-dependent
builders and the gold→record projection.

### 3.7 `dsm/match/score.py` — REWRITE

```python
def score_candidate(
    candidate: Candidate,
    scorecard: TargetProfileScorecard,
    *,
    lm: dspy.LM | None = None,
    adjacency_map: dict[str, list[str]] | None = None,
    weights: dict[str, float] | None = None,
    freshness_verdict: FreshnessVerdict | None = None,
) -> CandidateAssessment:
    """Score a candidate against a role (§6.8).

    LLM sub-scores + deterministic combine + adjacency + flags + citations.
    lm=None → stub sub-scores (for testing).
    """
```

**DSPy signature shape (illustrative):**

```python
class ScoreSignature(dspy.Signature):
    """Score a candidate against a role. Cite all claims with verbatim quotes."""
    role_requirements: str = dspy.InputField()
    candidate_profile: str = dspy.InputField()
    candidate_feedback: str = dspy.InputField()
    skill_match_score: float = dspy.OutputField(desc="0.0-1.0")
    feedback_score: float = dspy.OutputField(desc="0.0-1.0")
    hard_skill_coverage: float = dspy.OutputField(desc="fraction of hard skills matched")
    desired_skill_coverage: float = dspy.OutputField(desc="fraction of desired skills covered")
    narrative: str = dspy.OutputField(desc="1-2 sentence explanation")
    evidence: str = dspy.OutputField(desc="JSON array of {source, text} citations")
```

**Adjacency scoring (AD-033/035):**

```python
def _desired_skill_coverage(
    candidate: Candidate,
    desired_skills: list[SkillRequirement],
    adjacency_map: dict[str, list[str]],
) -> tuple[float, bool]:
    """Compute desired-skill coverage with adjacency partial credit.

    Returns (coverage_fraction, adjacency_used).
    Exact match = 1.0, adjacent = 0.5 (config-driven), else 0.
    """
```

**Citation verification (AD-073):**
Each `EvidenceCitation.text` is checked for verbatim presence in the candidate's
profile/feedback source text. Quotes not found are dropped.

**Flags:**
| Condition | Flag |
|-----------|------|
| `candidate.source == NEW_JOINER` | `UNVERIFIED_SKILLS` |
| `RollingOff.confidence` is `"low"` or `"medium"` | `ROLL_OFF_UNCERTAIN` |
| Any `feedback.entries[*].retention_flag == True` | `RETENTION_RISK` |
| Adjacency partial credit awarded | `ADJACENCY_USED` |
| `freshness_verdict.action == "warn"` | A freshness `Flag` (message from verdict) |

**Note on freshness flag:** `FlagType` currently has no `FRESHNESS_WARNING` member.
Two options: (a) use a generic message on an existing type, or (b) add
`FRESHNESS_WARNING` to `FlagType`. Since this is an additive, non-breaking change
to a frozen enum (like `HARD_SKILL_MISMATCH` was), it can be proposed as a minor
amendment. **Decision: add `FlagType.FRESHNESS_WARNING`** as part of T-000-ADR (no
separate ADR — it's additive to a non-gating enum, analogous to AD-088's additive
change to `ExclusionReason`).

### 3.8 `dsm/cli/commands.py` — REWRITE (full 9-step orchestrator)

`run_match` is rewritten to wire the full pipeline. Key changes:

1. **Candidate loading:** Build `GoldCandidateStore`, call `store.get(all_ids)` to
   hydrate serving `Candidate`s from gold.
2. **Parse + select:** `parse_demand(csv_path)` → select role by `--role-id`.
3. **Clarify:** `clarify_role(role, lm=lm)`.
4. **Freshness:** `check_freshness(...)` once per banner; `refuse` → exit non-zero.
5. **Gates → exact filter:** existing B-1 modules.
6. **Recall → rerank → score → rank:** new B-2 modules.
7. **Empty pool → NoMatchResult** at each narrowing stage (with `HARD_SKILL_MISMATCH`
   near-misses, AD-088).

**CLI signature:**

```python
def match(
    role_id: str = typer.Option(..., "--role-id"),
    demand_csv: Path = typer.Option(..., "--demand-csv"),
    gold_dir: Path = typer.Option(_GOLD_DEFAULT, "--gold-dir"),
) -> None:
```

**Freshness wiring:** `check_freshness` runs once per CSV banner. `refuse` → block.
`warn` → carry the `FreshnessVerdict` into `score_candidate`, which appends a
freshness `Flag` to every assessment.

**`dsm explain <role_id>`** — new Typer command. Re-runs the pipeline, dumps the
result's lineage (gate outcomes, filter outcomes, recall mode, rerank scores,
sub-scores, citations, `config_snapshot`, freshness verdict) as indented JSON.
No new persistence layer — reads what `ShortlistResult`/`NoMatchResult` already carry.

### 3.9 `dsm/index/indexer.py` — UPDATE (import path)

Update `from dsm.index.models import is_indexable, build_record` →
`from dsm.index.build import is_indexable, build_record`.

### 3.10 `config/default.yaml` — UPDATE

```yaml
index:
  milvus:
    db_path: "data/index/milvus.db"
    collection: "candidates"
    dense_metric: "IP"
  recall:
    enabled: false
    top_n: 100
  rerank:
    top_k: 10

adjacency_map:
  kotlin: [java]
  java: [kotlin]
  react: [nextjs]
  nextjs: [react]
  typescript: [javascript]
  javascript: [typescript]
  aws: [gcp]
  gcp: [aws]
  docker: [kubernetes]
  kubernetes: [docker]
  postgres: [mysql, sql]
  mysql: [postgres, sql]
  sql: [postgres, mysql]
  spark: [dbt, airflow]
  dbt: [spark, airflow]
  airflow: [spark, dbt]
  selenium: [cypress, playwright]
  cypress: [selenium, playwright]
  playwright: [selenium, cypress]
  llm: [rag, vector]
  rag: [llm, vector]
  vector: [llm, rag]
  ml: [scikit-learn]
  scikit-learn: [ml]
```

---

## 4. AD-091 index-model refactor — full touch-point list

### 4.1 `dsm/models.py`
- ADD `Grade` enum (moved from `dsm.ingest.models`)
- ADD `CandidateStore` protocol
- ADD `FlagType.FRESHNESS_WARNING`

### 4.2 `dsm/ingest/models.py`
- REMOVE `Grade` class definition
- ADD `from dsm.models import Grade` (re-import from shared location)
- All existing `Grade` usage in `dsm/ingest/` now resolves to the shared import

### 4.3 `dsm/index/models.py`
- CHANGE `from dsm.ingest.models import GoldCandidate, Grade` →
  `from dsm.models import Grade` (no `GoldCandidate` import)
- REMOVE `is_indexable`, `project_filter_fields`, `build_record` (→ `build.py`)
- ADD `RetrievedCandidate`
- KEEP `CandidateIndexRecord`, `FilterFields`, `IndexMetrics`

### 4.4 `dsm/index/build.py` — NEW
- `from dsm.ingest.models import GoldCandidate` (the build edge)
- MOVE `is_indexable`, `project_filter_fields`, `build_record` from `models.py`
- MOVE `build_embed_text`, `build_skill_set`, `included_skills` from `text_builder.py`

### 4.5 `dsm/index/text_builder.py`
- REMOVE `build_embed_text`, `build_skill_set`, `included_skills` (→ `build.py`)
- ADD `build_role_query_passage(scorecard)` (query-time, no ingest import)

### 4.6 `dsm/index/indexer.py`
- UPDATE imports: `build_record`, `is_indexable` from `dsm.index.build`
- UPDATE imports: `build_embed_text`, `build_skill_set` from `dsm.index.build`

### 4.7 `dsm/cli/commands.py`
- ADD `GoldCandidateStore` adapter class
- UPDATE `match()` to use `parse_demand` + `GoldCandidateStore` + full pipeline
- ADD `explain()` Typer command
- UPDATE `index()`: `is_indexable` from `dsm.index.build`

### 4.8 `pyproject.toml`
- ADD import-linter contract: `dsm.match ⊥ dsm.ingest`
- MODIFY existing `dsm.index` contract or add new: `dsm.index ⊥ dsm.ingest`
  with `dsm.index.build` as an **ignore_imports** exception

### 4.9 Tests affected
| File | Change |
|------|--------|
| `tests/index/test_index_models.py` | Update imports: projection helpers from `dsm.index.build` |
| `tests/index/test_text_builder.py` | Update: `build_embed_text`/`build_skill_set` from `dsm.index.build`; add `build_role_query_passage` tests |
| `tests/index/test_indexer.py` | Update imports if needed |
| `tests/cli/test_commands.py` (or similar) | Add orchestrator tests |
| `tests/ingest/test_models.py` | Verify `Grade` import still works from `dsm.models` |

---

## 5. Edge cases

| Edge case | Expected behaviour |
|-----------|--------------------|
| `description` is whitespace-only | Treated as empty → deterministic echo |
| LLM returns invalid sub-scores (out of range) | Clamp to [0.0, 1.0] and log warning |
| LLM returns a citation quote not found in source | Drop that citation; keep the rest |
| All citations for a candidate fail verification | Assessment still produced (with empty evidence) + warning |
| Rerank returns fewer scores than candidates | Log warning, mark unscored as `rerank_score=None` |
| Gold directory is empty | No candidates loaded; immediate `NoMatchResult` |
| `--role-id` not found in parsed CSV | Error message + exit non-zero |
| Adjacency map is empty | No partial credit awarded; exact match only |
| Candidate has no feedback entries | `feedback_score` defaults to 0.0; `RETENTION_RISK` never fires |
| Pool empty after exact filter but before rerank | `NoMatchResult` with hard-skill-gap near-misses |
| All candidates fail scoring (LLM errors) | Empty assessments → `NoMatchResult` |

---

## 6. Eval cases to add (for future `c-002-query-eval-harness`)

| Invariant | Test |
|-----------|------|
| **Evidence cited** | Every claim in the narrative has a matching `EvidenceCitation` |
| **No PII leak** | Candidate `name`/`email` never appear in any text sent to a provider |
| **Determinism** | Same input + config + mocked LM → identical `ShortlistResult` |
| **Hard skill not cleared by adjacency** | A candidate missing a HARD skill is excluded even when adjacent skill exists |
| **Gates respected** | A gated-out candidate never appears in the shortlist |
| **Adjacency fires only on desired** | `ADJACENCY_USED` never fires for a hard skill |
| **Score weights** | `combined_score == 0.7 * skill_match_score + 0.3 * feedback_score` (± float epsilon) |
| **Rerank fallback** | When `EmbedError` raised, all candidates pass through with `rerank_score=None` |
| **Freshness flag** | When verdict is `warn`, every assessment carries a `FRESHNESS_WARNING` flag |

---

## 7. Dependencies

No new dependencies beyond `docs/tech.md`. All modules use:
- Python stdlib (`csv`, `datetime`, `pathlib`, `re`, `logging`)
- Pydantic v2 (already pinned)
- DSPy (already pinned — typed signatures for clarify + score)
- structlog (already pinned)
- `modal` (already pinned — `EmbedClient` for rerank)
- `pymilvus` + `milvus-lite` (already pinned — hybrid recall)
