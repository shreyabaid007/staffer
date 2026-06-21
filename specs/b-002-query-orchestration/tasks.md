# B-002 Query Orchestration — Tasks

> **Lane:** B · **Slice:** B-2
> One task = one commit. `make check` GREEN after each.

---

## T-000 · ADR sign-off gate

Ratify AD-089 (deferred hybrid recall), AD-090 (seniority as soft signal), AD-091
(`CandidateStore` port + ingest-free index models + `match/index ⊥ ingest` import
contract) in `docs/decision.md`. AD-091 is a frozen/shared-contract touch (AD-060)
— **STOP for human sign-off before proceeding.**

Also add `FlagType.FRESHNESS_WARNING` to `dsm/models.py` (additive, non-breaking
change to a frozen enum — same pattern as AD-088's `ExclusionReason` addition).

**Acceptance:** AD-089/090/091 recorded in `docs/decision.md` with correct next IDs.
`FlagType.FRESHNESS_WARNING` noted in the AD-091 entry or a brief addendum.

---

## T-001 · Move `Grade` to `dsm/models.py` + add `CandidateStore` protocol + `FlagType.FRESHNESS_WARNING`

Apply the AD-091 shared-contract changes:

1. Move `Grade` enum from `dsm/ingest/models.py` to `dsm/models.py`.
2. In `dsm/ingest/models.py`, replace the class with `from dsm.models import Grade`.
3. Add `CandidateStore` protocol to `dsm/models.py`:
   ```python
   class CandidateStore(Protocol):
       def get(self, candidate_ids: list[str]) -> list[Candidate]: ...
   ```
4. Add `FRESHNESS_WARNING = "freshness_warning"` to `FlagType`.

Update any tests that import `Grade` from `dsm.ingest.models` to verify it still
resolves correctly.

**Acceptance:** `make check` GREEN. `Grade` importable from both `dsm.models` and
`dsm.ingest.models`. `CandidateStore` importable. `FlagType.FRESHNESS_WARNING` exists.

---

## T-002 · Extract `dsm/index/build.py` (ingest-dependent builders + projections)

Create `dsm/index/build.py` and move into it:

1. From `dsm/index/models.py`: `is_indexable`, `project_filter_fields`, `build_record`
2. From `dsm/index/text_builder.py`: `included_skills`, `build_embed_text`, `build_skill_set`

`dsm/index/build.py` imports `GoldCandidate` from `dsm.ingest.models` (the build edge).
`dsm/index/models.py` keeps `CandidateIndexRecord`, `FilterFields`, `IndexMetrics` —
now importing `Grade` from `dsm.models` instead of `dsm.ingest.models`.
`dsm/index/text_builder.py` is now empty of write-time builders (query-time builder added in T-008).

Update imports in:
- `dsm/index/indexer.py` → import from `dsm.index.build`
- `dsm/cli/commands.py::index()` → import `is_indexable` from `dsm.index.build`
- All tests that import the moved functions

**Acceptance:** `make check` GREEN. `dsm/index/models.py` no longer imports
`dsm.ingest.models`. All existing index tests pass.

---

## T-003 · Add `match/index ⊥ ingest` import contract

Add the import-linter contract to `pyproject.toml`:

```toml
[[tool.importlinter.contracts]]
name = "Index must not depend on ingest (except build edge)"
type = "forbidden"
source_modules = ["dsm.index"]
forbidden_modules = ["dsm.ingest"]
ignore_imports = ["dsm.index.build -> dsm.ingest.models"]
```

Verify the existing `dsm.match ⊥ dsm.ingest` constraint — `dsm.match` already has
no ingest imports (confirmed in B-1). If the contract doesn't exist yet, add it:

```toml
[[tool.importlinter.contracts]]
name = "Match must not depend on ingest"
type = "forbidden"
source_modules = ["dsm.match"]
forbidden_modules = ["dsm.ingest"]
```

**Acceptance:** `make check` GREEN. Import-linter verifies both contracts pass.
`dsm.index.build → dsm.ingest.models` is the only exempted edge.

---

## T-004 · Add `RetrievedCandidate` model

Add `RetrievedCandidate` to `dsm/index/models.py`:

```python
class RetrievedCandidate(BaseModel, frozen=True):
    candidate_id: str
    dense_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
```

Add basic model tests in `tests/index/test_index_models.py`.

**Acceptance:** `make check` GREEN. `RetrievedCandidate` importable and frozen.

---

## T-005 · Clarify LLM path (`dsm/match/clarify.py`)

Update `clarify_role` to accept an optional `lm: dspy.LM | None`:

1. When `role.description` is empty/None/whitespace → deterministic echo (existing).
2. When `role.description` is non-empty and `lm` is provided → bounded DSPy
   `Signature` over the LM (`temperature=0`). Parse output into
   `TargetProfileScorecard` with updated hard/desired skills and
   `clarification_notes`.
3. LLM failure → fall back to deterministic echo + log warning.
4. No redaction (§7).

Create `tests/match/test_clarify.py`:
- Echo path (empty description)
- Echo path (description but no LM)
- LLM path (mocked DSPy — fixed response)
- LLM failure → fallback echo + warning logged
- No redaction assertions (description passed verbatim)

**Acceptance:** `make check` GREEN. All FR-1 ACs pass. `clarify.py` imports only
`dsm.models`, `dspy`, `structlog`, stdlib.

---

## T-006 · Score + combine (`dsm/match/score.py`)

Rewrite `score_candidate` with:

1. **DSPy sub-scores:** When `lm` is provided, invoke `ScoreSignature` over the LM
   (`temperature=0`) to get `skill_match_score`, `feedback_score`, `narrative`, and
   `evidence` citations. Candidate text is PII-free by construction (ingestion
   redacted).
2. **Deterministic combine:** `combined_score = w_skill * skill_match_score + w_feedback * feedback_score`
   (weights from `weights` param, default `{"skill": 0.7, "feedback": 0.3}`).
3. **Citation verification (AD-073):** Each `EvidenceCitation.text` checked for
   verbatim presence in the candidate's profile_summary/feedback. Unverifiable
   quotes dropped.
4. **Hard/desired coverage:** Compute from the scorecard's skill requirements vs
   candidate's skills.
5. **Adjacency partial credit (AD-033/035):** `_desired_skill_coverage()` —
   exact desired = 1.0, adjacent (via `adjacency_map`) = 0.5, else 0. `ADJACENCY_USED`
   fires only when partial credit is actually awarded.
6. **Flags:** `UNVERIFIED_SKILLS` (new joiner), `ROLL_OFF_UNCERTAIN` (low/medium
   confidence), `RETENTION_RISK` (retention_flag), `FRESHNESS_WARNING` (warn verdict),
   `ADJACENCY_USED`.
7. **LLM error → log + skip** (return `None`, caller filters).

When `lm=None` → stub sub-scores (for testing / no-LM mode).

Create `tests/match/test_score.py`:
- Sub-score extraction (mocked DSPy)
- Combine arithmetic: `0.7*skill + 0.3*feedback`
- Adjacency partial credit + `ADJACENCY_USED` flag firing
- Citation verification: valid quote kept / invalid quote dropped / mixed
- Flag generation: each flag type
- LLM error → skip (returns None)
- Stub mode (lm=None)

**Acceptance:** `make check` GREEN. All FR-5 ACs pass.

---

## T-007 · Recall (deferred) in `dsm/index/retrieve.py`

Add `hybrid_recall()`:

1. When `enabled=False` (default) → passthrough: wrap each pool candidate as
   `RetrievedCandidate(candidate_id=..., dense_score=None, ...)`.
2. When `enabled=True` → embed role query via `EmbedClient.embed(mode="query")`,
   run dense top-N + BM25 top-N + RRF fusion.
3. `EmbedError` → fallback to passthrough + log warning.

Create `tests/index/test_retrieve_recall.py`:
- Recall OFF → passthrough, all scores None
- Recall ON (mocked Milvus + EmbedClient) → fused scores populated
- RRF determinism
- EmbedError → exhaustive fallback

**Acceptance:** `make check` GREEN. All FR-3 ACs pass.

---

## T-008 · Role query passage + rerank in `dsm/index/retrieve.py` + `text_builder.py`

**Part A:** Add `build_role_query_passage(scorecard)` to `dsm/index/text_builder.py`:
- Build from skills (hard + desired names) + min_proficiency-derived seniority +
  clarification_notes. Capability-only, PII-free.
- Add test asserting role/candidate builders share the skill span contract.

**Part B:** Add `rerank()` to `dsm/index/retrieve.py`:
1. Build role query passage.
2. Call `EmbedClient.rerank(query, passages)` → scores.
3. Attach `rerank_score` to each `RetrievedCandidate`.
4. Sort by `rerank_score` desc, truncate to `top_k`.
5. `EmbedError` → pass through unranked (`rerank_score=None`, no truncation),
   log warning.

Create `tests/index/test_retrieve_rerank.py`:
- Cross-encoder rerank (mocked EmbedClient) → scores populated, sorted, truncated
- Rerank error → pass through unranked (`rerank_score=None`)
- Ordering verified

Add `build_role_query_passage` tests to `tests/index/test_text_builder.py`.

**Acceptance:** `make check` GREEN. All FR-4 and FR-8 ACs pass.

---

## T-009 · `GoldCandidateStore` adapter in `dsm/cli/`

Create the `GoldCandidateStore` adapter:

1. New class in `dsm/cli/commands.py` (or `dsm/cli/candidate_store.py` if cleaner):
   ```python
   class GoldCandidateStore:
       def __init__(self, gold_dir: Path) -> None: ...
       def get(self, candidate_ids: list[str]) -> list[Candidate]: ...
   ```
2. Reads gold via `dsm.ingest.goldstore.read_gold` + `list_gold_ids`.
3. Hydrates each `GoldCandidate` into a serving `Candidate`:
   - `Skill.proficiency` from `MergedSkill`
   - `feedback` from gold's `FeedbackExtraction` list → `FeedbackSignals`
   - `profile_summary` from gold
   - `location` from gold's `Sourced[Location].value`
   - `availability` from gold's `Sourced[AvailabilityState].value`
   - `name`/`email` from vault refs (carried for final rendering, never sent to LLM)
4. At POC scale, hydrate the full pool up front.

Create `tests/cli/test_candidate_store.py`:
- Hydration from a mock gold entity → valid serving `Candidate`
- Missing gold → empty list
- Fields correctly mapped

**Acceptance:** `make check` GREEN. All FR-2 ACs pass. `GoldCandidateStore` is in
`dsm/cli/` (composition root — may import `dsm/ingest/`).

---

## T-010 · Config update + adjacency map seed

Update `config/default.yaml`:

1. Add `index.recall.enabled: false` and `index.recall.top_n: 100`
2. Add `index.rerank.top_k: 10`
3. Seed `adjacency_map` with AD-035 entries (JVM/Frontend/Cloud/Containers/SQL/Data/Test/GenAI/ML)
   — replacing the empty `{}`.

Update `dsm/config.py` if needed (the loader should handle the new nested keys).

**Acceptance:** `make check` GREEN. Config loads cleanly with new keys.

---

## T-011 · Full 9-step orchestrator (`dsm/cli/commands.py`)

Rewrite `run_match` + `match()` CLI command:

1. `match()` accepts `--role-id`, `--demand-csv`, `--gold-dir`.
2. Parse demand CSV → select role by `--role-id` (not found → error + exit 1).
3. Build `GoldCandidateStore(gold_dir)`, hydrate candidates.
4. Clarify role (LLM if `description` non-empty, else echo).
5. Freshness guard (B-1): `refuse` → exit 1; `warn` → carry verdict.
6. Gate pre-filter (B-1).
7. Exact hard-skill filter (B-1).
8. Empty-pool check → `NoMatchResult` with near-misses.
9. Recall (passthrough when OFF).
10. Rerank (cross-encoder).
11. Score each surviving candidate; collect assessments (skip LLM failures).
12. Rank (B-1 `rank_assessments`).
13. Empty assessments → `NoMatchResult`.
14. Print `ShortlistResult` / `NoMatchResult` JSON.

**Freshness wiring:** `warn` verdict → pass to `score_candidate` → freshness
`Flag` on every assessment.

**Config:** Read `config.weights`, `config.adjacency_map`, `config.index.recall`,
`config.index.rerank`, `config.models` for `config_snapshot`.

Create `tests/cli/test_orchestrator.py`:
- Full 9-step (all mocked: LM, EmbedClient, gold store)
- Empty-pool at each narrowing stage → `NoMatchResult`
- Freshness refuse → exit
- Freshness warn → flag on every assessment
- `config_snapshot` attached
- Role not found → error

**Acceptance:** `make check` GREEN. All FR-6 ACs pass. `dsm match --role-id ... --demand-csv ... --gold-dir ...` runs the full pipeline.

---

## T-012 · Explain CLI (`dsm explain <role_id>`)

Add `explain` Typer command to `dsm/cli/commands.py`:

1. Re-runs `run_match` with the same args.
2. Dumps the result's lineage as indented JSON:
   - Gate/filter exclusions
   - Recall mode (exhaustive / hybrid)
   - Rerank scores (if available)
   - Per-candidate: sub-scores, flags, narrative, citations
   - `config_snapshot`
   - Freshness verdict

Create `tests/cli/test_explain.py`:
- Lineage dump structure
- Per-candidate breakdown
- NoMatchResult lineage

**Acceptance:** `make check` GREEN. All FR-7 ACs pass.

---

## T-013 · Final verification + cleanup

1. Run full `make check`. Confirm all tests green, all import contracts pass.
2. Verify `dsm match --role-id ROLE-STUB-01` still works (backwards compat with
   stub path or updated to real pipeline — document which).
3. Verify no `dsm.ingest` imports in `dsm/match/*` or `dsm/index/*` (except
   `dsm/index/build.py`).
4. Verify all ADR refs in touched modules are current.
5. Clean up any dead code from the refactor (`dsm/index/stub.py` may be removable
   if the orchestrator no longer uses it).

**Acceptance:** `make check` GREEN. All acceptance criteria from `requirements.md`
met. No regressions. Lane file `docs/progress.B.md` updated via `/handoff`.
