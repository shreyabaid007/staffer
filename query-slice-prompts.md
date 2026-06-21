# Query-Time Pipeline — Implementation Slice Prompts

> Two execution prompts that together implement the full query-time serving pipeline
> described in `ee-query-architecture.md`. Each prompt is self-contained: scope,
> objectives, deliverables, acceptance criteria, and the spec files to write before
> any code.

---

## Corrections applied (vs. the original draft)

This file supersedes the original draft prompts. Changes made after review against the
repo + `ee-query-architecture.md`:

1. **Specs renamed to Lane B** (`b-001…` / `b-002…`) — query-time retrieval is Lane B;
   the `q-` prefix isn't a sanctioned lane (`docs/structure.md`).
2. **`Notes/Constraints` needs no redaction and rides the existing `OpenRole.description`
   field** — it's role-requirement free text authored by the demand side, not candidate
   PII. No frozen-contract amendment for it; clarify reads `role.description` directly. The
   whole demand-side redaction wiring (§7) is dropped. `Client` is an org name (not
   candidate PII); tokenizing it is a deferred client-confidentiality choice, not required
   by golden rule 3.
3. **`SkillDepth` / `SkillRequirement` are REUSED, not re-defined** — both are already
   frozen in `dsm/models.py` (`docs/structure.md`: models live once). `dsm/match/models.py`
   gains only the genuinely new intermediates.
4. **`rank.py` is KEPT as-is** — the existing `rank_assessments(assessments, role_id,
   exclusion_log, top_k, config_snapshot)` already implements the §6.10 sort. The original
   2-arg signature was a regression.
5. **The `Location` migration enumerates every touch point** (incl. the Milvus collection
   schema) and pins `onsite_cities` ↔ `ARRAY<VARCHAR>` handling.
6. **The freshness decision tree is pinned** to resolve the §6.3 table-vs-mermaid conflict.
7. **`ExclusionReason.HARD_SKILL_MISMATCH`** is added so the exact filter can log
   hard-skill-gap exclusions (AD-088, resolved — add the member).
8. **Adjacency scoring is fully specified** in Q-2; the `config.adjacency_map` seed is added.
9. **Config keys corrected** — weights stay top-level (`weights.skill/feedback`), not
   `scoring.weights.*`; recall keys added.
10. **A `T-000-ADR` sign-off gate task** opens each spec; **seniority (AD-090) moved to Q-2**
    (it's a scoring signal, irrelevant to Q-1's deterministic foundation).

> **Doc note:** `ee-query-architecture.md` has been **synced** to these decisions — proposed ADRs
> renumbered to **AD-086…AD-091**, demand-side redaction removed (`Notes`→`description`, no
> redaction), `RetrievedCandidate.rerank_score` made optional, a new **§6.0 candidate-materialisation**
> section added (the root gap), and §12 open questions extended (query-passage builder, batch output,
> eval-harness ownership). The architecture and these prompts are now consistent; the remaining
> open items have now been **resolved with recommended options** (candidate-loading via a
> `CandidateStore` port, `HARD_SKILL_MISMATCH` added, `Grade`/index-record home, single-role batch,
> query-passage field set, eval-harness as a separate slice) — each is still **ratified at the
> slice's `T-000-ADR` gate** where it touches a frozen contract.

---

## Slice B-1 (was Q-1): Deterministic Foundation — Demand Parse + Gates + Exact Filter + Rank

### Context

You are implementing the first of two query-time pipeline slices for the
Demand–Supply Matcher. The full architecture is in `ee-query-architecture.md`; this
slice covers **steps 1, 3, 4, 5, and 9** — everything that is deterministic and
LLM-free. The second slice (B-2) will add the LLM-dependent stages (clarify, score)
and the retrieval precision stages (recall, rerank), then wire the full orchestrator.

Before you begin: read `docs/progress.md` (index), your lane file, `CLAUDE.md` (the
rules), `docs/decision.md`, `docs/structure.md`, and `ee-query-architecture.md`
(the architecture you are implementing).

### Scope

| Step | Name | Architecture ref | Module | Status |
|------|------|-----------------|--------|--------|
| 1 | **Parse demand** | §6.1 | `dsm/match/demand.py` | NEW |
| 3 | **Freshness guard** | §6.3 | `dsm/match/freshness.py` | NEW |
| 4 | **Gate pre-filter** | §6.4 + AD-086 | `dsm/match/gates.py` | REWRITE — `_location_passes` only (c-001 deprecated, AD-085) |
| 5 | **Exact hard-skill filter** | §6.5 | `dsm/index/retrieve.py` | NEW (exact filter only this slice) |
| 9 | **Rank** | §6.10 | `dsm/match/rank.py` | **KEEP as-is** — already implements §6.10; verify + re-bless under the ratified design |
| — | **Query-time models** | §5 | `dsm/match/models.py` | NEW (new intermediates only — reuse frozen types) |
| — | **Location model amendment** | §6.4 / AD-086 | `dsm/models.py` | AMEND frozen contract |
| — | **ExclusionReason amendment** | §6.5 / AD-088 | `dsm/models.py` | AMEND frozen contract (add `HARD_SKILL_MISMATCH`) |

**Out of scope for B-1:**
- Step 2 (Clarify — LLM-dependent, B-2)
- Step 6 (Hybrid recall — deferred behind flag, B-2)
- Step 7 (Rerank — cross-encoder/LLM, B-2)
- Step 8 (Score + combine — LLM sub-scores, B-2)
- Full orchestrator wiring (B-2 — B-1 delivers tested modules, not the CLI chain)
- `dsm explain` CLI (B-2)
- Open Roles CSV fixture in `data/raw/demand/` (§12 open question #4; stub/synthetic test fixtures are fine)

### Objectives

1. **Parse** an Open Roles CSV into typed `OpenRole`s + a `DemandParseOutcome` with
   the banner `demand_as_of` date and skip accounting (§6.1). `Notes / Constraints` →
   `OpenRole.description` (the existing free-text field — no new field, no redaction).
2. **Guard freshness** — compare `demand_as_of` vs supply `valid_as_of`, producing
   ok/warn/refuse per the pinned decision tree below. Refuse blocks the run.
3. **Gate** candidates deterministically on location + availability, with the
   **AD-086 location model** (`remote_within_country` + `onsite_cities` replacing the
   overloaded `remote_eligible`). This is a frozen-contract amendment to
   `dsm/models.py::Location` — ratify it as a new ADR first.
4. **Exact-filter** hard skills via `skill_set` set membership + proficiency floor
   (§6.5). Cosine/adjacency never consulted for hard skills (AD-033/072). Hard-skill
   gaps are logged as `ExclusionReason.HARD_SKILL_MISMATCH`.
5. **Rank** assessments deterministically: `combined_score` desc → `hard_skill_coverage`
   desc → `desired_skill_coverage` desc → `candidate.email` asc; truncate to `top_k`.
   (The existing `rank_assessments` already does this — verify, don't rewrite.)

### Deliverables

#### 1. Spec files (write first, stop for review)

Create `specs/b-001-query-deterministic/` with `requirements.md` → `design.md` →
`tasks.md`. Follow the format in `docs/structure.md` and the precedent set by
`specs/a-005-index-upsert/`. The **first task is `T-000-ADR`** (see below) — STOP for
sign-off before any code.

- **`requirements.md`** — EARS-form acceptance criteria for each objective. Reference
  the architecture section numbers and existing ADRs.
- **`design.md`** — modules touched, the `Location` + `ExclusionReason` Pydantic
  amendments, the full migration touch-point list (below), data-flow diagram, edge
  cases, **eval cases to add** (gates-respected, hard-skill-not-cleared-by-adjacency).
- **`tasks.md`** — ordered, atomic, independently testable tasks; one task = one commit.

#### 2. ADRs to ratify (in `docs/decision.md`, next IDs start at AD-086)

- **`T-000-ADR` (gate task — do this first, STOP for human sign-off):**
- **AD-086** — Split `Location.remote_eligible` into `remote_within_country` +
  `onsite_cities`. Frozen-contract amendment (AD-060). **Supersedes AD-063a / AD-020
  location-gate semantics** and ingestion's `CandidateIndexRecord.remote_eligible`
  filter facet → **cross-lane** (touches Lane A index records). Requires sign-off.
- **AD-087** — Query-time as-of freshness guard (ok/warn/refuse). Reuses
  `config.reconcile.max_staleness_days`; touches no model.
- **AD-088 (RESOLVED — add it)** — Add `ExclusionReason.HARD_SKILL_MISMATCH`. The exact filter
  must record *why* a candidate was dropped so the no-match path can explain it, but the frozen enum
  has only `LOCATION_MISMATCH` / `AVAILABILITY_MISMATCH`. **Decision: add the member** (frozen-contract
  amendment, AD-060 — ratify at this gate). Hard-skill-gap near-misses rank below availability misses
  (§6.5). Rejected: returning exclusions with no reason (would make hard-skill no-matches unexplainable).

> (Seniority-as-soft-signal moved to B-2 / AD-090 — it has no role in this deterministic slice.)

#### 3. Implementation modules

| File | What it does |
|------|-------------|
| `dsm/match/models.py` (NEW) | `OpenRolesBanner`, `DemandParseOutcome` **only**. **REUSE** the frozen `SkillDepth` + `SkillRequirement` from `dsm/models.py` (do **not** redefine them — `docs/structure.md`). |
| `dsm/match/freshness.py` (NEW) | `FreshnessVerdict` model + `check_freshness(demand_as_of, valid_as_of, start_date, max_staleness_days) → FreshnessVerdict`. Pure datetime arithmetic, no LLM. |
| `dsm/match/demand.py` (NEW) | Parse Open Roles CSV → `DemandParseOutcome`. Banner as-of, skill split (HARD vs DESIRED), `Co-location` → `co_location_required`, `Notes / Constraints` → `OpenRole.description`, `Location` → AD-086 model. `Priority` orders the batch (sort `roles`), not a match signal. `Client`/`Sector` retained for future dense context (no live consumer while recall is OFF). log+skip invalid rows; missing/unparseable banner **blocks**. Pure Python, no LLM, no `dsm/ingest/` import. |
| `dsm/models.py` (AMEND) | `Location`: replace `remote_eligible: bool` with `remote_within_country: bool = False` + `onsite_cities: frozenset[str] = frozenset()`. `ExclusionReason`: add `HARD_SKILL_MISMATCH` (AD-088). |
| `dsm/match/gates.py` (REWRITE — `_location_passes` only) | `filter_candidates` keeps its structure (location first, availability second, short-circuit G-OUT-2, `(EligiblePool, ExclusionLog)` output). Rewrite **only** the location predicate: onsite gate (`co_location_required=True`) passes iff `role.city is not None and (candidate.city == role.city OR role.city in candidate.onsite_cities)` (case-insensitive); distributed gate (`co_location_required=False`) passes iff `candidate.country == role.country`. `remote_within_country` never clears an onsite gate. Stays pure — imports only `dsm.models` + stdlib. |
| `dsm/index/retrieve.py` (NEW) | `exact_hard_skill_filter(pool, hard_skills, store) → (filtered_pool, exclusions)`. `skill_set` set membership + `ProficiencyLevel` ordinal floor; BM25 sparse for rare tokens (e.g. `dbt`, `CKA`). Excluded candidates get an `Exclusion(reason=HARD_SKILL_MISMATCH, …)`. Deterministic, LLM-free; store-read error blocks (the filter is never silently skipped). |
| `dsm/match/rank.py` (**KEEP**) | No rewrite. The existing `rank_assessments(assessments, role_id, exclusion_log, top_k, config_snapshot)` already implements the §6.10 sort/tie-break/top-k and is config-free (AD-064). Verify it matches the ratified design; refresh its docstring's ADR refs if needed. |

#### 4. Tests

| Test file | Coverage |
|-----------|----------|
| `tests/match/test_demand.py` | Banner parse, skill split (HARD/DESIRED), co-location, `Notes`→`description`, `Priority` batch order, malformed rows logged+skipped+counted, **missing banner blocks** |
| `tests/match/test_freshness.py` | The four pinned cases: (1) supply fresher (`staleness ≤ 0`) → ok; (2) `staleness > max` → refuse; (3) `0 < staleness ≤ max` AND `start < valid` → warn; (4) else → ok |
| `tests/match/test_gates.py` | Onsite: `role.city is None` → excluded; `onsite_cities={"Chennai"}` + role Chennai + candidate Pune → **pass**; `remote_within_country=True` + onsite role + Pune → **fail** (ROLE-02 invariant); case-insensitive membership. Distributed: same-country pass. Availability window, short-circuit, empty pool. |
| `tests/index/test_retrieve.py` | Exact filter: `skill_set` membership (inline) + proficiency floor; BM25 rare-token path (vs temp Milvus Lite, per `tests/index/` pattern); `HARD_SKILL_MISMATCH` exclusion logged; empty pool → exclusions only |
| `tests/match/test_rank.py` | Deterministic sort, email tie-break, top_k truncation, empty input (regression guard only — no behaviour change) |

#### 5. Migration: the `Location` amendment (AD-086) — full touch-point list

Splitting `remote_eligible` ripples beyond the model. Update **all** of:

- `dsm/ingest/silver.py::parse_location` — map `Chennai-open=Yes` → `onsite_cities={"Chennai"}` (keep home `city`); `"Remote (India)"` → `remote_within_country=True, city=None`; plain city (e.g. `"Pune"`) → both defaults.
- `dsm/index/models.py` — `CandidateIndexRecord`, `FilterFields`, `project_filter_fields`, `build_record`: replace `remote_eligible` with `remote_within_country: bool` + `onsite_cities: list[str]` (**store as `list[str]`, not `frozenset`** — Milvus has no set type; rebuild the `frozenset` only when hydrating a `Location`).
- `dsm/index/milvus_store.py` — the collection schema `add_field("remote_eligible", BOOL)` (line ~72) → `remote_within_country` BOOL + `onsite_cities` `ARRAY<VARCHAR>`; the insert row dict (line ~132). `ensure_collection` rebuilds the test `.db`, so no prod data migration is in scope — say so.
- `dsm/cli/commands.py` — `build_near_misses` location-miss docstring/`gap_summary` (line ~48-88): "not open to relocation" wording no longer holds; reframe as "city not in role's onsite set."
- All tests/fixtures referencing `remote_eligible`: `tests/test_models.py`, `tests/fixtures/__init__.py`, `tests/match/test_gates.py`, `tests/ingest/test_silver_*.py`, `tests/ingest/test_models.py`, `tests/index/test_index_models.py`, `tests/index/test_milvus_store.py`.

> **Cross-lane note (AD-086):** this touches Lane A's index records + ingest silver and
> Lane C's gates. Coordinate / sign off before code (architecture §13). Handoff lands in
> the lane file resolved from `.claude/lane`.

### Acceptance criteria

- [ ] `T-000-ADR` ratified (AD-086/087/088) in `docs/decision.md` before any dependent code
- [ ] `make check` GREEN (format, lint, typecheck, all tests, import contracts)
- [ ] Demand CSV with a valid banner parses into typed `OpenRole`s; `Notes`→`description`; invalid rows logged+skipped+counted
- [ ] Missing/unparseable banner blocks the run (non-zero exit)
- [ ] Freshness guard matches the pinned decision tree (ok / warn / refuse)
- [ ] Onsite gate passes only `role.city is not None and (city == role.city OR role.city in onsite_cities)`; `remote_within_country` does NOT clear an onsite gate
- [ ] Distributed gate passes any same-country candidate
- [ ] Exact filter requires `hard_skills ⊆ skill_set` + proficiency floor; adjacency never consulted; gaps logged as `HARD_SKILL_MISMATCH`
- [ ] Empty pool at any stage → exclusions flow cleanly (no exception); no-match assembly itself is B-2
- [ ] `gates.py` imports nothing from `pii/`, `index/`, `ingest/`, or LLM code (import-linter)
- [ ] No new dependencies beyond `docs/tech.md`
- [ ] All existing tests pass — no regressions from the `Location` / `ExclusionReason` amendments

### Constraints

- **Spec before code.** Write `specs/b-001-query-deterministic/{requirements,design,tasks}.md` and stop for sign-off.
- **`T-000-ADR` first.** Frozen-contract amendments (AD-086, AD-088) ratified before dependent code.
- **One task = one commit**, imperative, referencing the spec.
- **Do not import `dsm/ingest/`** from `dsm/match/` or `dsm/index/` (§10). (The CSV parser mirrors `ingest/parse/csv.py` but does not import it.)
- **Do not wire the full orchestrator** — that is B-2. Deliver tested, independently runnable modules.

---

## Slice B-2 (was Q-2): Retrieval + Scoring + Full Orchestration

### Context

You are implementing the second of two query-time pipeline slices. B-1 delivered the
deterministic foundation: demand parse, freshness guard, gates (with the AD-086
location model), exact hard-skill filter, and rank. This slice adds the
**LLM-dependent** and **retrieval precision** stages, then wires the **full 9-step
orchestrator** and the **explain CLI**.

Before you begin: read `docs/progress.md`, your lane file, `CLAUDE.md`,
`docs/decision.md`, `ee-query-architecture.md`, and the B-1 spec
(`specs/b-001-query-deterministic/`).

### Scope

| Step | Name | Architecture ref | Module | Status |
|------|------|-----------------|--------|--------|
| 2 | **Clarify** | §6.2 | `dsm/match/clarify.py` | UPDATE (add bounded LLM path; reads `role.description`, **no redaction**) |
| 6 | **Hybrid recall** | §6.6 | `dsm/index/retrieve.py` | ADD (deferred, flag OFF by default) |
| 7 | **Rerank** | §6.7 | `dsm/index/retrieve.py` + `embed_client.py` | ADD (cross-encoder default) |
| 8 | **Score + combine** | §6.8 | `dsm/match/score.py` | REWRITE (sub-scores + flags + citations) |
| — | **Orchestrator** | §4, §10 | `dsm/cli/commands.py` | REWRITE (full 9-step pipeline) |
| — | **Explain CLI** | §9 | `dsm/cli/commands.py` | NEW (`dsm explain <role_id>`) |
| — | **Retrieval models** | §5 | `dsm/index/models.py` | ADD (`RetrievedCandidate`) |

**Out of scope for B-2:**
- Everything B-1 delivered (demand parse, freshness, gates, exact filter, rank)
- **Demand-side PII redaction** — demand input carries no candidate PII; `Notes`/`Client`
  are not redacted (see PII note below)
- Open Roles CSV fixture in `data/raw/demand/` (deferred, §12)
- LLM-rerank variant (documented alternative; cross-encoder is the default)
- Enabling hybrid recall (flag stays OFF; the stage is fully specified but dormant)
- `FlagType.SKILL_CONFLICT` (§12 open question #2; deferred)
- `Grade` on the serving `Candidate` (§12 open question #3; sourced from index record)
- **Wiring `make eval`** — B-2 adds eval *cases* in `design.md`, but configuring the Promptfoo +
  DeepEval invariants (gates-respected, hard-skill-not-cleared-by-adjacency, evidence-cited,
  no-PII-leak, determinism) is a **separate quality slice** (e.g. `c-002-query-eval-harness`,
  arch §12 #9). `make check` green is this slice's DoD; `make check-all` follows once eval is wired.

### Objectives

1. **Clarify** an `OpenRole` into a `TargetProfileScorecard` — deterministic echo when
   `role.description` (the parsed `Notes / Constraints`) is empty; a bounded DSPy
   signature over `PseudonymisedLM` (temp 0) when free text is present. **No redaction**
   — demand free text describes the role, not a candidate. LLM failure → deterministic
   echo + logged warning (never drops the role).
2. **Recall** (hybrid, deferred) — fully implement dense + BM25 + RRF in `retrieve.py`
   but keep `index.recall.enabled = false`. OFF → exhaustive passthrough to rerank; ON →
   narrows before rerank; error → fall back to exhaustive.
3. **Rerank** with the cross-encoder (`bge-reranker-base` via `EmbedClient.rerank()`).
   Build the role query passage symmetric to the ingestion passage. Rerank failure →
   order by the structured combine alone (log a warning).
4. **Score + combine** each candidate: a bounded DSPy signature emits sub-scores
   (`skill_match_score`, `feedback_score`, `hard_skill_coverage`,
   `desired_skill_coverage`), a 1–2 sentence `narrative`, and verified `EvidenceCitation`s
   (AD-073). Python computes `combined_score = 0.7·skill + 0.3·feedback` (weights from
   `config.weights`). Flags: `UNVERIFIED_SKILLS`, `ADJACENCY_USED`, `ROLL_OFF_UNCERTAIN`,
   `RETENTION_RISK`, freshness warn-flag. LLM error on one candidate → log+skip it.
5. **Orchestrate** the full 9-step pipeline in `dsm/cli/commands.py`: parse → clarify →
   freshness → gate → exact filter → (recall) → rerank → score → rank. Empty pool at any
   post-gate stage → `NoMatchResult` with ordered, capped near-misses (AD-063b/c/d).
   Attach `config_snapshot` to every result.
   - **Freshness wiring (H6):** `check_freshness` runs once per CSV banner at the command edge.
     `refuse` → exit non-zero / block the run (no shortlist). `warn` → carry the `FreshnessVerdict`
     into `score_candidate`, which appends a freshness `Flag` to **every** assessment. The per-role
     `start_date` comparison is a soft signal in score, never a gate.
   - **Batch (H9 — RESOLVED, AD-050):** `dsm match` parses the whole CSV but matches **one role per
     invocation** (selected by `--role-id`); freshness is evaluated against the CSV banner. A batch
     loop over `list[OpenRole]` is **deferred** (AD-050 scopes the MVP to single-role matching).
   - **Candidate materialisation (RESOLVED, §6.0 / AD-091):** before the gate, the CLI hydrates real
     `Candidate`s via the `CandidateStore` port + `GoldCandidateStore` adapter (see the commands.py
     row below). This is the load-bearing prerequisite.
6. **Explain** — `dsm explain <role_id>` re-runs the pipeline and dumps the result's
   lineage (gate/filter outcomes, recall mode, rerank scores, sub-scores, citations,
   `config_snapshot`, freshness verdict). No new persistence layer — read what
   `ShortlistResult`/`NoMatchResult` already carry (§9).

### Deliverables

#### 1. Spec files (write first, stop for review)

Create `specs/b-002-query-scoring-orchestration/` with `requirements.md` → `design.md`
→ `tasks.md`. First task is `T-000-ADR`. `design.md` must give explicit module
signatures, the DSPy signature shapes (clarify + score), the retrieval data flow, the
orchestrator sequence diagram, the candidate-loading decision (below), edge cases, and
eval cases (evidence-cited, no-PII-leak, determinism).

#### 2. ADRs to ratify

- **AD-089** — Defer hybrid recall behind `index.recall.enabled` (arch §6.6/§13). Ship exhaustive
  rerank at POC scale; flip ON when the post-filter pool exceeds ~150.
- **AD-090** — Seniority is a soft signal, never a gate (moved from B-1; it's a scoring concern).
  Sourced from `CandidateIndexRecord.grade` + `years_experience`; **no** `Candidate` amendment.
- **AD-091 (RESOLVED — `CandidateStore` port + ingest-free index models)** — three coupled moves
  (arch §6.0/§13): (1) `CandidateStore` protocol in `dsm/models.py` + `GoldCandidateStore` adapter in
  `dsm/cli/`; (2) move `Grade` → `dsm/models.py`, make `CandidateIndexRecord`/`RetrievedCandidate`
  ingest-free, relocate the gold→record projection helpers (`project_filter_fields`/`build_record`/
  `is_indexable`, which need `GoldCandidate`) to the `dsm index` **build edge** (exempt like the CLI);
  (3) add the `dsm/match/* , dsm/index/* ⊥ dsm/ingest/*` import contract to `pyproject.toml` (CLI +
  build edge exempt). Frozen/shared-contract touch → ratify at this gate.
- Verify AD-086/087/088 (B-1) are ratified; fill gaps.

#### 3. Implementation modules

| File | What it does |
|------|-------------|
| `dsm/match/clarify.py` (UPDATE) | `role.description` non-empty → DSPy `Signature` over `PseudonymisedLM` (temp 0) → `TargetProfileScorecard` (may add/strengthen hard vs desired skills, capture constraints in `clarification_notes`; cannot invent/relax a gate). Empty → deterministic echo (current stub). LLM failure → echo + logged warning. **No redaction call** — demand free text is not candidate PII. |
| `dsm/index/text_builder.py` (ADD) | `build_role_query_passage(scorecard) -> str` — the role-side passage, **symmetric to the candidate `build_embed_text`**, built from **skills (hard+desired names) + `min_proficiency`-derived seniority + `clarification_notes`** (capability-only; the fields the scorecard already carries). **No `TargetProfileScorecard` amendment** (no domain/sector field — §12 #7 resolved). Apply the BGE query prefix at embed time. Add a test asserting the role/candidate builders share the skill span contract. |
| `dsm/index/retrieve.py` (ADD) | `hybrid_recall(pool, role_query, store, config) → list[RetrievedCandidate]` — dense top-N (`EmbedClient.embed(mode="query")` + Milvus) ⊕ BM25 top-N ⊕ RRF. Gated by `index.recall.enabled` (default false → passthrough). `rerank(query, passages, embed_client) → list[RetrievedCandidate]` — cross-encoder joint scores via `EmbedClient.rerank()`, **truncated to `index.rerank.top_k`** (narrows which candidates get LLM-scored; NOT the final sort — step 9 rank decides order). Rerank error (`EmbedError`) → pass the pool through **unranked** (`rerank_score=None`, no truncation), log a warning; final order still from rank. Candidate `embed_text`/`dense_vector` are fetched from the store by `candidate_id` (carried by the hydrated `Candidate`, §6.0/AD-091). |
| `dsm/index/models.py` (ADD) | `RetrievedCandidate(candidate_id: str, dense_score: float \| None = None, bm25_score: float \| None = None, rrf_score: float \| None = None, rerank_score: float \| None = None)` — **all scores optional**: recall OFF → dense/bm25/rrf None; rerank error → rerank_score None. |
| `dsm/match/score.py` (REWRITE) | DSPy signature emits sub-scores + narrative + citations (verified quotes, AD-073 — a quote not found in source is dropped, never emitted). Python combine `0.7·skill + 0.3·feedback` (from `config.weights`). **Desired-skill coverage uses adjacency partial credit** (AD-033/035): exact desired skill = 1.0, adjacent (via `config.adjacency_map`) = 0.5 (config-driven), else 0; firing the `ADJACENCY_USED` flag **only when partial credit is actually awarded**. Other flags: `UNVERIFIED_SKILLS` (new joiner), `ROLL_OFF_UNCERTAIN`, `RETENTION_RISK`, freshness. LLM error on one candidate → log+skip. |
| `dsm/cli/commands.py` (REWRITE) | `run_match` wires the full 9-step sequence (parse/clarify/freshness at the command edge; gate→…→rank inside). Reads config, injects deps (embed client, LM), empty-pool → `NoMatchResult` (with near-misses) at each narrowing stage, attaches `config_snapshot`. **Candidate loading:** build `GoldCandidateStore` (adapter) here and inject `list[Candidate]` into the pipeline (§6.0/AD-091); single-role per invocation (AD-050). |
| `dsm/cli/commands.py` (ADD) | `dsm explain <role_id>` — re-run `run_match`, dump the result's lineage (no new store). |
| `config/default.yaml` (UPDATE) | Add `index.recall.enabled: false`, `index.recall.top_n: 100`. **Keep weights at top level** (`weights.skill`/`weights.feedback` already exist and are read by the orchestrator — do **not** introduce a `scoring.weights.*` path). Seed `adjacency_map` with the AD-035 entries (JVM/Frontend/Cloud/Containers/SQL/Data/Test/GenAI/ML). |

> **RESOLVED — candidate loading via a `CandidateStore` port (AD-091, arch §6.0).** `run_match`
> consumes `list[Candidate]`, but today it uses `dsm.ingest.stub.get_stub_candidates` and the store
> holds PII-free `CandidateIndexRecord`s. **Decision:** define a `CandidateStore` **protocol** in
> `dsm/models.py` (`get(candidate_ids) -> list[Candidate]`); `dsm/match` + `dsm/index` depend on the
> interface only. Implement a `GoldCandidateStore` **adapter** in `dsm/cli/` (the composition root —
> the only layer that may import `dsm/ingest/`) that reads gold via `goldstore`, hydrates `Skill`
> proficiency + feedback + `profile_summary` + location/availability, and carries `candidate_id`
> (the store key for the Milvus `skill_set`/`embed_text` lookups). **Name/email excluded** — carry a
> pseudonymised id through the pipeline; fetch identity from the vault only at final human-facing
> rendering (no-PII-leak invariant). At POC scale hydrate the full pool up front; narrow to retrieved
> ids once recall (AD-089) is enabled.

#### 4. Tests

| Test file | Coverage |
|-----------|----------|
| `tests/match/test_clarify.py` | Echo (empty `description`), LLM path (mocked DSPy), LLM-failure fallback. (No redaction assertions — demand text isn't redacted.) |
| `tests/index/test_retrieve_recall.py` | Recall ON (mocked Milvus), OFF (passthrough → scores None), RRF determinism, recall error → exhaustive fallback |
| `tests/index/test_retrieve_rerank.py` | Cross-encoder rerank (mocked `EmbedClient`), rerank error → structured-combine order (`rerank_score=None`), ordering |
| `tests/match/test_score.py` | Sub-score extraction (mocked DSPy), combine arithmetic, adjacency partial credit + `ADJACENCY_USED` firing, flag generation, **citation verification: valid quote kept / invalid quote dropped / mixed**, LLM-error skip |
| `tests/cli/test_orchestrator.py` | Full 9-step (all mocked), empty-pool at each narrowing stage → `NoMatchResult`, `config_snapshot` attached |
| `tests/cli/test_explain.py` | Lineage dump structure, per-candidate breakdown |

#### 5. PII boundary (demand side, §7) — no redaction required

- The demand CSV carries **no candidate PII**. `Notes / Constraints` (now
  `OpenRole.description`) and `Client` describe the *role*, not a consultant, so they
  reach the clarify LLM **without redaction**. Golden rule 3 is about consultant
  name/email, which never appear in demand input.
- The inherited guarantee is unchanged: candidate text reaching Modal/OpenRouter is
  PII-free **by construction** (ingestion redacted it; name/email live only in the vault).
- `Client` is an org name, not candidate PII. Tokenizing it for *client confidentiality*
  is a separate, deferred choice — not wired this slice.
- > **Stub note:** `dsm/pii/pseudonymised_lm.py` is still a pass-through stub. Routing all
  > LLM calls through it satisfies "all provider access via `PseudonymisedLM`"; the live
  > Presidio anonymiser is a separate, pending task (`docs/progress.C.md`). Tests mock the
  > LM. State this dependency in the spec rather than assuming a live provider.

### Acceptance criteria

- [ ] `T-000-ADR` ratified (AD-089/090/091) before dependent code
- [ ] `make check` GREEN
- [ ] `dsm match --role-id <id>` runs the full 9-step pipeline and prints a `ShortlistResult` or `NoMatchResult` JSON (subject to the candidate-loading decision)
- [ ] Clarify: empty `description` → echo; free-text → bounded LLM path; LLM failure → fallback echo
- [ ] Hybrid recall OFF by default; ON → dense + BM25 + RRF fused scores; error → exhaustive fallback
- [ ] Rerank orders by cross-encoder score; failure → structured combine (`rerank_score=None`)
- [ ] Score emits sub-scores + narrative + verified citations; `combined_score = 0.7·skill + 0.3·feedback`
- [ ] Every `EvidenceCitation.text` verified present in source; unverifiable claims dropped
- [ ] Adjacency partial credit applied to desired-skill coverage; `ADJACENCY_USED` fires only when credit is awarded
- [ ] Flags surface trade-offs: `UNVERIFIED_SKILLS`, `ADJACENCY_USED`, `ROLL_OFF_UNCERTAIN`, `RETENTION_RISK`
- [ ] Empty pool at any post-gate stage → `NoMatchResult` with ordered, capped near-misses
- [ ] `config_snapshot` attached to every result
- [ ] `dsm explain <role_id>` dumps full per-role lineage
- [ ] No PII reaches OpenRouter unpseudonymised; no name/email reaches Modal (by construction)
- [ ] All LLM calls go through `PseudonymisedLM`; `temperature=0` on every call
- [ ] Deterministic: same input + config + model versions → identical output (LM mocked to fixed output in tests)
- [ ] No new dependencies beyond `docs/tech.md`
- [ ] `dsm/match/* , dsm/index/* ⊥ dsm/ingest/*` import contract added (CLI orchestrator exempt)

### Constraints

- **Spec before code.** Write `specs/b-002-query-scoring-orchestration/{requirements,design,tasks}.md` and stop for sign-off.
- **`T-000-ADR` first.**
- **One task = one commit.**
- **B-1 is a prerequisite.** Consume demand parse, freshness, gates, exact filter, rank — do not re-implement.
- **Do not import `dsm/ingest/`** from `dsm/match/` or `dsm/index/` (§10). The CLI orchestrator may.
- **All provider access through `PseudonymisedLM`** — no direct OpenRouter/Modal calls from match/score.
- **Hybrid recall stays OFF.** Implement fully; `index.recall.enabled = false`. The flip to ON is a config change, not a code change.
- **Cross-encoder is the default reranker.** The LLM-rerank variant is documented, not wired.

---

## Dependency graph

```
B-1 (deterministic foundation)
 ├── demand.py        — Parse demand CSV (Notes → OpenRole.description)
 ├── freshness.py     — As-of guard (pinned decision tree)
 ├── models.py        — Location split (AD-086) + ExclusionReason.HARD_SKILL_MISMATCH (AD-088)
 ├── gates.py         — Location (onsite/distributed) + availability gates
 ├── retrieve.py      — Exact hard-skill filter
 └── rank.py          — KEEP (already implements §6.10)
        │
        ▼
B-2 (retrieval + scoring + orchestration)
 ├── clarify.py       — Optional LLM clarify (reads description; no redaction)
 ├── retrieve.py      — Hybrid recall (OFF) + rerank (cross-encoder)
 ├── score.py         — LLM sub-scores + deterministic combine + adjacency
 ├── commands.py      — Full 9-step orchestrator + explain CLI + CandidateStore adapter (§6.0/AD-091)
 └── config/          — recall keys + adjacency_map seed (weights stay top-level)
```

B-1 delivers tested, independently callable modules. B-2 consumes them, adds the
LLM/retrieval stages, and wires everything into the CLI.
