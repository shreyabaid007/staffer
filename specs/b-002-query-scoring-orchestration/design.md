# Design — b-002 Query-Time Scoring + Retrieval + Full Orchestration

> How B-2 is built. Reuses the frozen `dsm/models.py` contracts and the B-1 modules
> (`demand.py`/`freshness.py`/`gates.py`/`retrieve.exact_hard_skill_filter`/`rank.py`); adds the
> clarify LLM path, the role query passage, hybrid recall (OFF) + rerank, the score rewrite, the
> `CandidateStore` port + gold adapter, the full orchestrator, and `dsm explain`. Architecture:
> `ee-query-architecture.md` §4, §6.0, §6.2, §6.6–§6.10, §9, §10, §13.

## Modules touched

| File | Change | What |
|------|--------|------|
| `dsm/models.py` | AMEND | Add `Grade` (moved from `dsm.ingest.models`) + `CandidateStore` protocol. Re-export `Grade` from `dsm.ingest.models` for back-compat (single definition, shared home). |
| `dsm/index/models.py` | AMEND | Add `RetrievedCandidate`; import `Grade` from `dsm.models` (drop `dsm.ingest` import); **move** `is_indexable`/`project_filter_fields`/`build_record` out (they need `GoldCandidate`). |
| `dsm/index/build.py` | NEW (build edge) | New home for the gold→record projection helpers (`is_indexable`/`project_filter_fields`/`build_record`); may import `dsm.ingest` (exempt build edge, like the CLI). `dsm/index/indexer.py` + the CLI import from here. |
| `dsm/index/text_builder.py` | ADD | `build_role_query_passage(scorecard) -> str` + a shared skill-span helper used by both builders. |
| `dsm/index/retrieve.py` | ADD | `hybrid_recall(...)` (OFF by default) + `rerank(...)`. Keep B-1's `exact_hard_skill_filter`. |
| `dsm/match/clarify.py` | UPDATE | Add the bounded DSPy clarify path + injected predictor seam; keep the echo. |
| `dsm/match/score.py` | REWRITE | Bounded DSPy sub-scores + Python combine + adjacency + flags + citation verification. |
| `dsm/cli/commands.py` | REWRITE + ADD | `GoldCandidateStore` adapter, full 9-step `run_match`, `match` rewrite, new `explain`. |
| `config/default.yaml` | UPDATE | `index.recall.*`, `index.rerank.top_k`, seed `adjacency_map`. |
| `pyproject.toml` | UPDATE | Add the `match`/`index` ⊥ `ingest` import contract (CLI + build edge exempt). |

## Reused frozen contracts (do NOT redefine)

`OpenRole`, `TargetProfileScorecard`, `Candidate`, `Skill`, `Location`, `AvailabilityState`,
`EligiblePool`, `Exclusion`, `ExclusionLog`, `ExclusionReason` (incl. `HARD_SKILL_MISMATCH`),
`CandidateAssessment`, `Flag`, `FlagType`, `EvidenceCitation`, `EvidenceSource`, `ShortlistResult`,
`NearMiss`, `NoMatchResult`, `SkillDepth`, `SkillRequirement`, `ProficiencyLevel`. B-1 intermediates
`OpenRolesBanner`, `DemandParseOutcome`, `FreshnessVerdict` are reused as-is.

---

## AD-091 — `CandidateStore` port + ingest-free index models (the load-bearing move)

**Three coupled moves, done together in `T-001` so `make check` stays green:**

1. **`CandidateStore` protocol** in `dsm/models.py`:

   ```python
   from typing import Protocol, runtime_checkable

   @runtime_checkable
   class CandidateStore(Protocol):
       def get(self, candidate_ids: list[str]) -> list[Candidate]: ...
   ```

   `dsm/match` + `dsm/index` depend on this interface only; the concrete adapter is injected at
   the CLI composition root.

2. **`Grade` moves to `dsm/models.py`** (shared enum). `dsm/ingest/models.py` re-imports it
   (`from dsm.models import Grade`) so there is a single definition and no ingest churn elsewhere.
   `dsm/index/models.py` imports `Grade` from `dsm.models` and **drops** its `dsm.ingest` import.

3. **Projection helpers relocate to the build edge.** `is_indexable`/`project_filter_fields`/
   `build_record` need `GoldCandidate`, so they move from `dsm/index/models.py` to a new
   `dsm/index/build.py`. `dsm/index/build.py` is a **build/composition edge** — exempt from the new
   import contract (it may import `dsm.ingest`), exactly like the CLI orchestrator. `dsm/index/
   indexer.py` and `dsm/cli/commands.py::index` update their imports to `dsm.index.build`.

**New import contract** (`pyproject.toml`):

```toml
[[tool.importlinter.contracts]]
name = "Match and index must not depend on ingest"
type = "forbidden"
source_modules = ["dsm.match", "dsm.index"]
forbidden_modules = ["dsm.ingest"]
```

Exemptions are by module *not* being in `source_modules`: `dsm.cli` and `dsm.index.build` are the
two composition/build edges and are not listed as sources, so they remain free to import
`dsm.ingest`. (Import-linter `forbidden` contracts apply to the named source modules and their
submodules; `dsm.index.build` is a submodule of `dsm.index`, so it must be added as an
**explicit allowed import** — see Risk R-1 below for the exact mechanism.)

> **Risk R-1 (verify early, in T-001):** `dsm.index.build` is a submodule of `dsm.index`, and the
> contract names `dsm.index` as a source — so `dsm.index.build → dsm.ingest` would be flagged.
> Import-linter `forbidden` contracts support `ignore_imports` for exactly this. The contract will
> list `ignore_imports = ["dsm.index.build -> dsm.ingest.**"]` (and the analogous indexer import is
> *through* `build`, so only `build` touches `dsm.ingest`). T-001 confirms `lint-imports` passes
> with this exemption before any other code lands.

---

## `dsm/index/models.py` — `RetrievedCandidate`

```python
class RetrievedCandidate(BaseModel, frozen=True):
    """A candidate surviving the exact filter, carrying recall/rerank provenance (§5)."""
    candidate_id: str
    dense_score: float | None = None   # None when recall deferred (AD-089)
    bm25_score: float | None = None
    rrf_score: float | None = None     # None when recall deferred
    rerank_score: float | None = None  # None on rerank error (§6.7 fallback)
```

All scores optional: recall OFF → dense/bm25/rrf None; rerank error → rerank_score None.

---

## `dsm/index/text_builder.py` — role query passage (§6.6/§6.7, §12 #7)

```python
def build_role_query_passage(scorecard: TargetProfileScorecard) -> str: ...
```

- Composed (deterministic, sorted) from: hard+desired **skill names** (the same phrase shape the
  candidate builder uses), a `min_proficiency`-derived seniority phrase, and `clarification_notes`.
  Capability-only — no identity, no client/sector, **no new scorecard field**.
- **Symmetry contract:** extract the candidate builder's skill-phrase formatting into a shared
  helper (e.g. `_skill_phrase(name, proficiency)`) used by both `build_embed_text` and
  `build_role_query_passage`, so the role and candidate spans are built the same way. A test
  asserts both builders agree on the skill-span format.
- The BGE **query** instruction prefix is **not** baked into the string here — it is applied at
  embed time via `EmbedClient.embed(mode="query")` (asymmetric passage/query, AD-072), mirroring how
  the candidate passage uses `mode="passage"`.

---

## `dsm/match/clarify.py` — bounded LLM clarify (§6.2)

```python
class RoleClarification(dspy.Signature):
    """Refine an open role into a target scorecard (see config/prompts/role_clarification)."""
    title: str = dspy.InputField()
    description: str = dspy.InputField()
    required_skills: list[SkillRequirement] = dspy.InputField()
    clarification: ScorecardClarification = dspy.OutputField()  # match-local DSPy output type

ClarifyPredictor = Callable[[OpenRole], ScorecardClarification]

def make_clarify_predictor(lm: dspy.LM) -> ClarifyPredictor: ...   # real path (CLI), not tests

def clarify_role(role: OpenRole, *, predict: ClarifyPredictor | None = None)
        -> TargetProfileScorecard: ...
```

- `predict is None` **or** `role.description` empty → **echo** scorecard (current behaviour).
- `role.description` non-empty + `predict` supplied → call `predict(role)`; merge the
  `ScorecardClarification` (refined hard/desired skill lists + `clarification_notes`) into a
  `TargetProfileScorecard`. **`location`, `co_location_required`, `start_date`,
  `availability_window_days` always come from the parsed role/config — the LLM cannot set a gate.**
- `predict` raising → log a warning + return the echo scorecard (FR-2-AC-4). The role is never
  dropped.
- `ScorecardClarification` is a **match-local** DSPy output model (in `dsm/match/models.py`) — not a
  frozen contract. It carries only `hard_depth_skills`/`desired_skills`/`clarification_notes`.
- **No redaction** — `clarify_role` does not import `dsm.pii.redact`; the LM is `PseudonymisedLM`
  (the sanctioned provider path), injected as `predict` (the same seam as `enrich`). Demand text is
  not candidate PII (§7).

---

## `dsm/index/retrieve.py` — recall (OFF) + rerank (§6.6/§6.7)

```python
def hybrid_recall(
    candidates: list[Candidate],
    role_query: str,
    store: MilvusIndexStore,
    embed_client: EmbedClient,
    config: dict[str, Any],
) -> list[RetrievedCandidate]: ...
```

- `config["index"]["recall"]["enabled"]` is `false` (default) → **passthrough**: one
  `RetrievedCandidate(candidate_id=…)` per input candidate, all scores `None` (FR-4-AC-2).
- `true` → dense top-N (`embed_client.embed([role_query], mode="query")` → Milvus dense search over
  the filtered `candidate_id`s) ⊕ BM25 top-N (over `skill_text`/`skill_set`) ⊕ **RRF** fuse
  (`rrf_score = Σ 1/(rrf_k + rank_i)`, `rrf_k` constant, deterministic); populate
  `dense_score`/`bm25_score`/`rrf_score`. Narrow to `recall.top_n`.
- `EmbedError`/store error while ON → fall back to passthrough + log warning (FR-4-AC-4).

```python
def rerank(
    role_query: str,
    candidates: list[RetrievedCandidate],
    store: MilvusIndexStore,
    embed_client: EmbedClient,
    top_k: int,
) -> list[RetrievedCandidate]: ...
```

- Fetch each candidate's `embed_text` from the store by `candidate_id` (the store key carried on
  the hydrated `Candidate`/`RetrievedCandidate`); call `embed_client.rerank(role_query, passages)`;
  set `rerank_score`; sort desc; **truncate to `top_k`** (`index.rerank.top_k`). This *narrows*
  which candidates get LLM-scored — it is **not** the final sort (FR-5-AC-2).
- `EmbedError` → return the input unranked (`rerank_score=None`, **no** truncation) + log warning
  (FR-5-AC-3). The final order is still produced by step 9.

**Store read path:** add a small read method to `MilvusIndexStore` (e.g.
`fetch_embed_texts(candidate_ids) -> dict[str, str]` and a dense-search helper) — additive, no
schema change (the `embed_text`/`dense`/`skill_text` fields already exist).

---

## `dsm/match/score.py` — sub-scores + deterministic combine (§6.8)

```python
class CandidateScoring(dspy.Signature):
    """Emit sub-scores + narrative + citations for one role–candidate pair (see config/prompts)."""
    role: TargetProfileScorecard = dspy.InputField()
    candidate_skills: list[str] = dspy.InputField()
    candidate_feedback: list[str] = dspy.InputField()
    profile_summary: str = dspy.InputField()
    assessment: ScoreExtraction = dspy.OutputField()  # match-local DSPy output type

ScorePredictor = Callable[[TargetProfileScorecard, Candidate], ScoreExtraction]

def make_score_predictor(lm: dspy.LM) -> ScorePredictor: ...      # real path (CLI), not tests

def score_candidate(
    candidate: Candidate,
    scorecard: TargetProfileScorecard,
    *,
    predict: ScorePredictor,
    config: dict[str, Any],
    freshness: FreshnessVerdict | None = None,
) -> CandidateAssessment | None: ...
```

- `ScoreExtraction` (match-local DSPy output, in `dsm/match/models.py`) carries
  `skill_match_score`, `feedback_score`, `narrative`, and `evidence: list[EvidenceCitation]`. The
  LLM emits sub-scores + narrative + citations **only**.
- **Python computes**, never the LLM (AD-030, tech.md rule 4):
  - `combined_score = config["weights"]["skill"]·skill_match + config["weights"]["feedback"]·feedback`.
  - `hard_skill_coverage` = fraction of `scorecard.hard_depth_skills` present in
    `candidate.skills` (exact, **no adjacency** — AD-033/FR-6-AC-7; reuse B-1's name-membership
    logic conceptually, but compute coverage here).
  - `desired_skill_coverage` = adjacency partial credit (AD-033/035): per desired skill, exact = 1.0,
    adjacent via `config["adjacency_map"]` = 0.5, else 0; averaged over desired skills (1.0 when no
    desired skills).
- **Citation verification (AD-073):** drop any `EvidenceCitation` whose `text` is not verbatim
  present (whitespace-normalised) in the candidate's source text (skills/feedback/profile_summary
  joined) — reuse the `_quote_present`/`_norm` approach from `enrich.py` (duplicated, not imported
  — `match ⊥ ingest`). Verified citations kept; the rest dropped (FR-6-AC-4).
- **Flags (FR-6-AC-5):**
  - `ADJACENCY_USED` — fired **only** when adjacency credit (0.5) was actually awarded to some
    desired skill.
  - `UNVERIFIED_SKILLS` — candidate is a new joiner (`candidate.source is CandidateSource.NEW_JOINER`).
  - `ROLL_OFF_UNCERTAIN` — `RollingOff.confidence == "low"`.
  - `RETENTION_RISK` — any `FeedbackEntry.retention_flag` is set.
  - freshness flag — when `freshness.action == "warn"`, attach a freshness `Flag` to every
    assessment.
- `predict` raising on one candidate → return `None`; the orchestrator filters `None` and counts the
  skip (FR-6-AC-6).

> **FlagType note:** there is no `SKILL_CONFLICT` member (§12 #2, deferred); resume↔feedback
> conflicts are not surfaced as a flag this slice. No `FlagType` amendment.

---

## `dsm/cli/commands.py` — adapter + orchestrator + explain

### `GoldCandidateStore` adapter (§6.0/AD-091)

```python
class GoldCandidateStore:                       # implements CandidateStore (structural)
    def __init__(self, gold_dir: Path): ...
    def get(self, candidate_ids: list[str]) -> list[Candidate]: ...
    def all_ids(self) -> list[str]: ...          # POC: hydrate the full pool up front
```

- Reads gold via `goldstore.read_gold`; hydrates a serving `Candidate`:
  - `location` ← `gold.location.value`; `availability` ← `gold.availability.value`.
  - `skills` ← `[Skill(name, proficiency or BEGINNER) for s in gold.skills if s.demonstrated is not
    False]` (FR-1-AC-4; mirrors AD-081). Proficiency `None` on a `MergedSkill` → default to a floor
    that still clears no `min_proficiency` it shouldn't — **decision pinned in T-000-ADR:** map
    `None` proficiency to `BEGINNER` (lowest), so a hard-skill floor is honoured conservatively.
  - `feedback` ← `FeedbackSignals(entries=[FeedbackEntry(...) from gold.feedback])` (source +
    retention_flag from `FeedbackExtraction.retention_requested`).
  - `profile_summary` ← derived from gold (projects/domains/seniority signals joined), or `None`.
  - `email = name = candidate_id` (FR-1-AC-3 — pseudonymised id; no raw identity).
  - skips tombstoned / non-indexable gold (no location/availability) — they cannot be gated.
- Lives in `dsm/cli/` (the composition root, the only layer allowed to import `dsm.ingest`).

### `run_match` (full 9-step, §4/§10)

```python
def run_match(
    candidates: list[Candidate],
    scorecard: TargetProfileScorecard,
    *,
    store: MilvusIndexStore | None,
    embed_client: EmbedClient | None,
    score_predict: ScorePredictor,
    config: dict[str, Any],
    freshness: FreshnessVerdict | None = None,
) -> ShortlistResult | NoMatchResult: ...
```

Sequence (parse/clarify/freshness happen at the `match` command edge, before `run_match`):

1. `gate` — `filter_candidates(candidates, scorecard)` → `(pool, gate_exclusions)`. Empty →
   `NoMatchResult` (near-misses from gate exclusions).
2. `exact filter` — `exact_hard_skill_filter(pool, scorecard.hard_depth_skills)` →
   `(filtered, hard_exclusions)`. Merge exclusion logs. Empty → `NoMatchResult` (near-misses now
   span availability + location + hard-skill, AD-088 ordering).
3. `recall` — `hybrid_recall(filtered.candidates, role_query, store, embed_client, config)` (OFF →
   passthrough). `role_query = build_role_query_passage(scorecard)`.
4. `rerank` — `rerank(role_query, retrieved, store, embed_client, config["index"]["rerank"]["top_k"])`.
   When `store`/`embed_client` is `None` (pure-unit path) → skip recall+rerank, score the filtered
   pool directly.
5. `score` — `score_candidate(c, scorecard, predict=score_predict, config=config,
   freshness=freshness)` for each reranked candidate (resolved back to the hydrated `Candidate` by
   `candidate_id`); drop `None`s (log+count skips).
6. `rank` — `rank_assessments(assessments, role_id, merged_exclusion_log, top_k, config_snapshot)`.

`config_snapshot` (FR-7-AC-5) extends the B-1 snapshot with recall mode + rerank model + freshness
verdict so `explain` can read it from the result.

`build_near_misses` is extended to handle `HARD_SKILL_MISMATCH` exclusions (AD-088 ordering:
availability misses → location misses → hard-skill misses, the last ordered by number of missing
hard skills then `candidate_id`). Gaps are recomputed from structured data, never parsed from
`Exclusion.detail`.

### `match` command (rewrite, §4)

```python
def match(role_id, csv_path=…, gold_dir=…, db_path=…) -> None:
    outcome = parse_demand(csv_path)                       # may raise → exit 1 on bad banner
    role = next((r for r in outcome.roles if r.role_id == role_id), None)
    if role is None: exit 1                                 # FR-7-AC-4
    store = GoldCandidateStore(gold_dir)
    candidates = store.get(store.all_ids())                # POC: full pool up front
    supply_valid_as_of = max valid_as_of over hydrated candidates
    verdict = check_freshness(outcome.banner.demand_as_of, supply_valid_as_of,
                              role.start_date, config.reconcile.max_staleness_days)
    if verdict.action == "refuse": echo(verdict.message); exit 1   # FR-7-AC-2
    scorecard = clarify_role(role, predict=_build_clarify_predictor(config))
    result = run_match(candidates, scorecard, store=_milvus(), embed_client=_embed(),
                       score_predict=_build_score_predictor(config), config=config,
                       freshness=verdict if verdict.action == "warn" else None)
    echo(result.model_dump_json(indent=2))
```

Predictor/embed/store builders follow the existing `_build_*_predictor` / `_build_embed_client`
pattern (live deps, monkeypatched in CLI tests).

### `explain` command (NEW, §9)

```python
def explain(role_id, csv_path=…, gold_dir=…, db_path=…) -> None: ...
```

Re-runs `run_match` and dumps lineage (no new store): freshness verdict, per-candidate gate/exact
outcomes (from the exclusion log + the eligible set), recall mode, rerank scores, sub-scores +
combine weights, citations, `config_snapshot`. For a no-match: reason + ordered near-misses with
recomputed gaps. Output is a structured JSON/echo of what the result already carries.

---

## Data flow (one role)

```
Open Roles CSV ──parse_demand──▶ OpenRole (──> select by --role-id)
                                    │
GoldCandidateStore.get(all_ids) ──▶ list[Candidate]   check_freshness(banner, supply, start)
                                    │                         │ refuse → exit 1
clarify_role(role, predict) ──▶ TargetProfileScorecard        │ warn  → carry verdict
                                    │                         ▼
                       filter_candidates ─▶ (pool, excl) ─empty─▶ NoMatchResult
                                    │
                exact_hard_skill_filter ─▶ (filtered, excl) ─empty─▶ NoMatchResult
                                    │
   build_role_query_passage ─▶ hybrid_recall (OFF→passthrough) ─▶ rerank (truncate top_k)
                                    │
                 score_candidate (LLM sub-scores + Python combine + flags + cited) [skip None]
                                    │
                 rank_assessments ─▶ ShortlistResult (config_snapshot)
```

---

## Edge cases

- **Empty CSV / bad banner** → `parse_demand` raises `ValueError` → `match` exits 1 (B-1 behaviour).
- **`--role-id` not in CSV** → exit 1, clear message (FR-7-AC-4).
- **Refuse freshness** → exit 1, operator message, no shortlist (FR-7-AC-2).
- **Warn freshness** → every assessment carries a freshness flag.
- **Empty pool after gate / after exact filter** → `NoMatchResult` with correctly-ordered
  near-misses (availability < location < hard-skill, AD-088).
- **All candidates skipped at score (LLM errors)** → empty assessments → `rank_assessments` yields
  a `ShortlistResult` with an empty `ranked_assessments` (not a no-match; the pool *was* eligible).
  Documented behaviour; the run notes the skip count.
- **Rerank error** → unranked passthrough; order from rank (FR-5-AC-3).
- **Recall ON + store error** → exhaustive fallback (FR-4-AC-4).
- **Clarify LLM error** → echo scorecard (FR-2-AC-4).
- **`MergedSkill.proficiency is None`** → hydrate as `BEGINNER` (conservative floor; T-000-ADR).
- **No desired skills** → `desired_skill_coverage = 1.0`, no `ADJACENCY_USED`.

---

## Eval cases to add (listed; wiring is `c-002`, §12 #9)

- **evidence-cited** — every `CandidateAssessment.evidence[].text` is verbatim-present in the
  candidate source; a fabricated/rotted quote is dropped (not emitted).
- **no-PII-leak** — no hydrated `Candidate.name`/`email` (here = `candidate_id`) and no raw
  identity reaches the score predictor or the embed/rerank client (assert the prompt inputs are
  `candidate_id`-only / capability-only).
- **determinism** — same CSV + supply + config + mocked LM → byte-identical `ShortlistResult`.
- **hard-skill-not-cleared-by-adjacency** — a candidate missing a hard skill but adjacent to it is
  excluded by the exact filter (B-1) and never resurrected by recall/score (B-2).
- **gates-respected** — recall/rerank/score never reorder a gate-failed candidate into the
  shortlist.
- **adjacency-flag** — `ADJACENCY_USED` fires iff adjacency credit was awarded.

---

## Test plan (mirrors `query-slice-prompts.md` B-2 §4)

| Test file | Coverage |
|-----------|----------|
| `tests/test_models.py` (extend) | `Grade` importable from `dsm.models`; `CandidateStore` protocol shape. |
| `tests/match/test_clarify.py` | Echo (empty description / no predictor), LLM path (mocked predictor), LLM-failure fallback. No redaction. |
| `tests/index/test_text_builder.py` (extend) | `build_role_query_passage` deterministic; role/candidate skill-span symmetry. |
| `tests/index/test_retrieve_recall.py` | Recall OFF (passthrough → scores None), ON (mocked Milvus + FakeEmbedClient: dense ⊕ BM25 ⊕ RRF), RRF determinism, recall error → exhaustive fallback. |
| `tests/index/test_retrieve_rerank.py` | Cross-encoder rerank (FakeEmbedClient), truncation to top_k, rerank error → unranked (`rerank_score=None`), ordering. |
| `tests/match/test_score.py` | Sub-score extraction (mocked predictor), combine arithmetic, adjacency partial credit + `ADJACENCY_USED`, flag generation (`UNVERIFIED_SKILLS`/`ROLL_OFF_UNCERTAIN`/`RETENTION_RISK`/freshness), citation verify (valid kept / invalid dropped / mixed), hard-skill-no-adjacency, LLM-error skip. |
| `tests/index/test_build.py` | Relocated `is_indexable`/`project_filter_fields`/`build_record` still pass (moved, not changed). |
| `tests/cli/test_store.py` | `GoldCandidateStore` hydration: skills exclude `demonstrated is False`; `email`/`name` = `candidate_id`; tombstoned/thin skipped; `MergedSkill.proficiency None` → BEGINNER. |
| `tests/cli/test_orchestrator.py` | Full 9-step (all mocked: store/embed/predictors); empty-pool at gate and at exact filter → `NoMatchResult`; rerank/recall fallbacks; `config_snapshot` attached; freshness warn → flags; refuse → exit. |
| `tests/cli/test_explain.py` | Lineage dump structure; per-candidate breakdown; no-match lineage. |

All tests inject mocks/Fakes — **no network or LLM** (NF-1). Reuse `tests/fixtures/` ROLE-01/02/03
scorecards + candidates; build gold fixtures in `tmp_path` for the store tests.
