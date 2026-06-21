# B-002 Query Orchestration — Requirements

> **Lane:** B (Reasoning) · **Slice:** B-2
> **Architecture ref:** `ee-query-architecture.md` §4, §6.0, §6.2, §6.6, §6.7, §6.8, §6.10, §9, §10, §12, §13
> **Prerequisite:** B-1 (`specs/b-001-query-deterministic/`, PR #18, merged)

---

## User story

As the staffing decision engine, given a demand-side Open Roles CSV and a real
supply-side gold layer, I can **clarify** role requirements via an optional LLM,
**load** real candidates from gold, **recall** and **rerank** them, **score**
each with cited sub-scores, and **rank** the result — producing a fully wired,
explainable shortlist through the 9-step pipeline, end to end.

---

## Functional requirements (EARS format)

### FR-1 · Clarify role (step 2) — `dsm/match/clarify.py`

**When** the system receives an `OpenRole` whose `description` is empty or `None`,
**the system shall** produce a `TargetProfileScorecard` via the **deterministic echo**
(the existing stub: partition `required_skills` by depth).

**When** the system receives an `OpenRole` whose `description` contains free text,
**the system shall** invoke a bounded DSPy typed signature over `PseudonymisedLM`
(`temperature=0`) to refine the scorecard — adding/strengthening hard vs desired
skills and capturing constraints in `clarification_notes`. The LLM **cannot** invent
a gate or relax one.

**Where** the LLM call fails (error/timeout),
**the system shall** fall back to the deterministic echo scorecard and attach a logged
warning. The role is never dropped for a clarify failure.

**The system shall not** redact `description` or `Client` — demand free text describes
the role, not a candidate (§7, no candidate PII on the demand side).

| AC | Criterion |
|----|-----------|
| FR-1-AC-1 | `description=None` → deterministic echo; scorecard matches current stub behaviour |
| FR-1-AC-2 | `description="must have led a payments platform"` → LLM path invoked; `clarification_notes` populated |
| FR-1-AC-3 | LLM error → deterministic echo + logged warning; role not dropped |
| FR-1-AC-4 | All LLM calls route through `PseudonymisedLM`; `temperature=0` |
| FR-1-AC-5 | No redaction of `description` or `Client` |
| FR-1-AC-6 | LLM cannot invent new gates or relax existing ones |

### FR-2 · Candidate materialisation (step 0) — `CandidateStore` port (AD-091)

**When** the system prepares to run the match pipeline,
**the system shall** load real `Candidate`s from gold via a `CandidateStore` protocol
(defined in `dsm/models.py`) with a `GoldCandidateStore` adapter injected at the CLI
composition root (`dsm/cli/commands.py`).

**The system shall** hydrate each `GoldCandidate` into a serving `Candidate` with
`Skill.proficiency`, feedback, `profile_summary`, location, and availability — but
**exclude** `name`/`email` from anything sent to a provider (the vault-only rendering
guarantee, §6.0 PII note).

| AC | Criterion |
|----|-----------|
| FR-2-AC-1 | `CandidateStore` is a `Protocol` in `dsm/models.py` with `get(candidate_ids) -> list[Candidate]` |
| FR-2-AC-2 | `GoldCandidateStore` adapter in `dsm/cli/` reads gold via `goldstore`, hydrates serving `Candidate` |
| FR-2-AC-3 | `dsm/match/*` and `dsm/index/*` depend on the protocol only, never on `dsm/ingest/` |
| FR-2-AC-4 | `name`/`email` on the hydrated `Candidate` are carried for final rendering but never sent to a provider (by construction — candidate text is PII-free at ingest) |
| FR-2-AC-5 | At POC scale, the full pool is hydrated up front |

### FR-3 · Hybrid recall (step 6, deferred) — `dsm/index/retrieve.py`

**When** `index.recall.enabled` is `true` (config),
**the system shall** compute dense top-N (`EmbedClient.embed(mode="query")` + Milvus)
⊕ BM25 top-N ⊕ RRF fusion (`rrf_score = Σ 1/(k + rank_i)`, deterministic) over the
gated, exact-filtered pool, producing `RetrievedCandidate`s with `dense_score`,
`bm25_score`, `rrf_score`.

**When** `index.recall.enabled` is `false` (default),
**the system shall** skip recall entirely (exhaustive passthrough to rerank).

**Where** an `EmbedError` occurs during recall,
**the system shall** fall back to the exhaustive path over the gated pool and log a
warning.

| AC | Criterion |
|----|-----------|
| FR-3-AC-1 | `recall.enabled=false` → all post-filter candidates pass through with `dense_score=None`, `bm25_score=None`, `rrf_score=None` |
| FR-3-AC-2 | `recall.enabled=true` → fused scores populated; RRF is deterministic |
| FR-3-AC-3 | `EmbedError` during recall → fallback to exhaustive, logged warning |
| FR-3-AC-4 | Recall is fully implemented but OFF by default (AD-089) |

### FR-4 · Rerank (step 7) — `dsm/index/retrieve.py`

**When** the system receives the post-recall candidate pool,
**the system shall** build a role query passage (`build_role_query_passage(scorecard)`)
and score each role–candidate pair jointly via `EmbedClient.rerank()` (the
`bge-reranker-base` cross-encoder on Modal), producing `rerank_score`s, and truncate
to `index.rerank.top_k` (config).

**Where** an `EmbedError` occurs during rerank,
**the system shall** pass the pool through **unranked** (each `rerank_score=None`, no
truncation), log a warning, and note "rerank unavailable". The final order is still
produced by step 9 on `combined_score`.

| AC | Criterion |
|----|-----------|
| FR-4-AC-1 | Cross-encoder scores each pair; candidates ordered by `rerank_score` desc |
| FR-4-AC-2 | Truncated to `index.rerank.top_k` candidates |
| FR-4-AC-3 | Rerank error → pass through unranked (`rerank_score=None`), logged warning |
| FR-4-AC-4 | Role query passage symmetric to the candidate passage (capability-only, PII-free) |
| FR-4-AC-5 | `rerank_score` is carried for lineage, not re-applied at rank (§6.7) |

### FR-5 · Score + combine (step 8) — `dsm/match/score.py`

**When** the system scores a candidate,
**the system shall** invoke a bounded DSPy signature over `PseudonymisedLM`
(`temperature=0`) that emits `skill_match_score`, `feedback_score`,
`hard_skill_coverage`, `desired_skill_coverage`, a 1–2 sentence `narrative`, and
`EvidenceCitation`s (verbatim quotes verified present in the source, AD-073).

**The system shall** compute `combined_score = 0.7 * skill_match_score + 0.3 * feedback_score`
in Python (weights from `config.weights`, AD-030). The LLM never does the arithmetic.

**The system shall** apply adjacency partial credit to desired-skill coverage (AD-033/035):
exact match = 1.0, adjacent (via `config.adjacency_map`) = 0.5 (config-driven), else 0.
`ADJACENCY_USED` flag fires **only** when partial credit is actually awarded.

**The system shall** surface trade-offs as `Flag`s: `UNVERIFIED_SKILLS` (new joiner, AD-032),
`ADJACENCY_USED` (AD-033), `ROLL_OFF_UNCERTAIN` (AD-022), `RETENTION_RISK` (AD-023),
freshness warn-flag (when `FreshnessVerdict.action == "warn"`).

**Where** the LLM errors on one candidate,
**the system shall** log + skip that candidate; the rest still rank.

**Where** a citation's quote is not found in the source,
**the system shall** reject the claim (drop the citation, AD-073); never emit unsourced.

| AC | Criterion |
|----|-----------|
| FR-5-AC-1 | Sub-scores are 0.0–1.0; `combined_score = 0.7*skill + 0.3*feedback` |
| FR-5-AC-2 | Every `EvidenceCitation.text` verified present in source; unverifiable quotes dropped |
| FR-5-AC-3 | Adjacency partial credit: exact desired = 1.0, adjacent = 0.5, else 0 |
| FR-5-AC-4 | `ADJACENCY_USED` fires only when partial credit is actually awarded |
| FR-5-AC-5 | `UNVERIFIED_SKILLS` flag on new joiners (AD-032) |
| FR-5-AC-6 | `ROLL_OFF_UNCERTAIN` flag when `RollingOff.confidence` is `"low"` or `"medium"` (AD-022) |
| FR-5-AC-7 | `RETENTION_RISK` flag when any feedback entry has `retention_flag=True` (AD-023) |
| FR-5-AC-8 | Freshness warn-flag when `FreshnessVerdict.action == "warn"` |
| FR-5-AC-9 | LLM error on one candidate → log + skip; rest still rank |
| FR-5-AC-10 | All LLM calls via `PseudonymisedLM`, `temperature=0` |
| FR-5-AC-11 | Candidate text reaching the LLM is PII-free by construction |

### FR-6 · Full 9-step orchestrator — `dsm/cli/commands.py`

**When** the user runs `dsm match --role-id <id>`,
**the system shall** execute the full 9-step pipeline: parse demand CSV → clarify →
freshness guard → gate pre-filter → exact hard-skill filter → (recall, if enabled) →
rerank → score → rank.

**The system shall** wire these stages:
1. Parse demand CSV to get `DemandParseOutcome` (B-1 `parse_demand`)
2. Select the role matching `--role-id` from the parsed roles (single-role per invocation, AD-050)
3. Clarify the role → `TargetProfileScorecard` (FR-1)
4. Load real candidates via `CandidateStore` (FR-2)
5. Check freshness (B-1 `check_freshness`); `refuse` → exit non-zero
6. Gate pre-filter (B-1 `filter_candidates`)
7. Exact hard-skill filter (B-1 `exact_hard_skill_filter`)
8. Recall (FR-3, default OFF → passthrough)
9. Rerank (FR-4)
10. Score + combine each surviving candidate (FR-5)
11. Rank (B-1 `rank_assessments`)

**Where** the pool is empty at any post-gate stage,
**the system shall** produce a `NoMatchResult` with ordered, capped near-misses
(AD-063b/c/d), including `HARD_SKILL_MISMATCH` near-misses (AD-088).

**The system shall** attach `config_snapshot` to every result.

**The system shall** accept `--demand-csv` (path to the Open Roles CSV) and
`--gold-dir` (path to the gold directory) as CLI options.

| AC | Criterion |
|----|-----------|
| FR-6-AC-1 | `dsm match --role-id <id>` runs the full 9-step pipeline |
| FR-6-AC-2 | Prints a valid `ShortlistResult` or `NoMatchResult` JSON |
| FR-6-AC-3 | Freshness `refuse` → exit non-zero, no shortlist |
| FR-6-AC-4 | Empty pool at any narrowing stage → `NoMatchResult` with near-misses |
| FR-6-AC-5 | `config_snapshot` attached to every result |
| FR-6-AC-6 | Accepts `--demand-csv` and `--gold-dir` CLI options |
| FR-6-AC-7 | Single-role per invocation (`--role-id` selects from parsed roles, AD-050) |

### FR-7 · Explain CLI — `dsm/cli/commands.py`

**When** the user runs `dsm explain <role_id>`,
**the system shall** re-run the pipeline and dump the result's full per-role lineage:
gate/filter outcomes, recall mode, rerank scores, sub-scores, citations,
`config_snapshot`, freshness verdict.

| AC | Criterion |
|----|-----------|
| FR-7-AC-1 | `dsm explain <role_id>` dumps full lineage |
| FR-7-AC-2 | Per-candidate breakdown: sub-scores, flags, narrative, citations |
| FR-7-AC-3 | Gate/filter exclusions listed |
| FR-7-AC-4 | Config snapshot and freshness verdict included |

### FR-8 · Role query passage builder — `dsm/index/text_builder.py`

**When** the system builds a role query passage for rerank,
**the system shall** construct a capability-only passage from the scorecard's skills
(hard + desired names), `min_proficiency`-derived seniority, and `clarification_notes`
— symmetric to the candidate `build_embed_text`.

| AC | Criterion |
|----|-----------|
| FR-8-AC-1 | Passage built from skills + seniority + clarification_notes |
| FR-8-AC-2 | PII-free by construction (no candidate identity) |
| FR-8-AC-3 | Symmetric to candidate passage in the skill span contract |

### FR-9 · Index-layer `match`/`index` ⊥ `ingest` import contract (AD-091)

**When** the import-linter runs,
**the system shall** enforce that `dsm/match/*` and `dsm/index/*` do not import
`dsm/ingest/*`. The CLI orchestrator (`dsm/cli/*`) is exempt.

| AC | Criterion |
|----|-----------|
| FR-9-AC-1 | `dsm.match` ⊥ `dsm.ingest` in `pyproject.toml` import-linter |
| FR-9-AC-2 | `dsm.index` ⊥ `dsm.ingest` in `pyproject.toml` import-linter |
| FR-9-AC-3 | `dsm.cli` is exempt (composition root) |
| FR-9-AC-4 | Existing code adjusted to satisfy the contract (Grade + index models made ingest-free) |

---

## Non-functional requirements

| ID | Requirement |
|----|-------------|
| NF-1 | `make check` GREEN after every commit (format, lint, typecheck, all tests, import contracts) |
| NF-2 | All LLM calls go through `PseudonymisedLM`; `temperature=0` on every call |
| NF-3 | No PII reaches OpenRouter unpseudonymised; no `name`/`email` reaches Modal |
| NF-4 | Deterministic: same input + config + model versions → identical output (LM mocked to fixed output in tests) |
| NF-5 | No new dependencies beyond `docs/tech.md` |
| NF-6 | One task = one commit, imperative, referencing the spec |
| NF-7 | Hybrid recall stays OFF by default; the flip to ON is a config change, not a code change |
| NF-8 | `dsm/match/*` and `dsm/index/*` must not import `dsm/ingest/*` (except the CLI/build edge) |

---

## ADRs to ratify (`T-000-ADR`)

| ADR | Amendment | Touches |
|-----|-----------|---------|
| **AD-089** | Defer hybrid recall behind `index.recall.enabled` (flag OFF); ship exhaustive rerank at POC scale | New config key + `dsm/index/retrieve.py` |
| **AD-090** | Seniority is a soft signal, never a gate; sourced from `CandidateIndexRecord.grade` + `years_experience` | No frozen model change |
| **AD-091** | `CandidateStore` port + `Grade`/`CandidateIndexRecord` made ingest-free + `match/index ⊥ ingest` import contract | `dsm/models.py` (protocol), `dsm/index/models.py` (refactor), `dsm/cli/` (adapter), `pyproject.toml` |

---

## Out of scope (not B-2)

- Demand-side PII redaction (demand input carries no candidate PII, §7)
- Open Roles CSV fixture in `data/raw/demand/` (deferred, §12)
- LLM-rerank variant (documented alternative; cross-encoder is the default)
- Enabling hybrid recall (flag stays OFF)
- `FlagType.SKILL_CONFLICT` (§12 open question #2; deferred)
- `Grade` on the serving `Candidate` (sourced from index record, §12 #3)
- Wiring `make eval` (separate quality slice `c-002-query-eval-harness`)
- Live `PseudonymisedLM` anonymiser (stub routes through it; live Presidio is Lane C)
