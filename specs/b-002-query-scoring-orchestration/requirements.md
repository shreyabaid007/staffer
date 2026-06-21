# Requirements — b-002 Query-Time Scoring + Retrieval + Full Orchestration

> Slice B-2. The second query-time slice: the **LLM-dependent** (clarify, score) and
> **retrieval-precision** (hybrid recall, rerank) stages, plus the **full 9-step orchestrator**
> and the **explain** CLI. B-1 (`specs/b-001-query-deterministic/`) delivered the deterministic
> foundation (demand parse, freshness, gates, exact filter, rank); this slice **consumes** it,
> never re-implements it. Architecture: `ee-query-architecture.md` §4, §6.0, §6.2, §6.6–§6.10,
> §9, §10. Source prompt: `query-slice-prompts.md` Slice B-2.

## User story

As a staffer matching one open role, I want the engine to clarify the role, retrieve and
rerank the gated/exact-filtered pool, score each candidate with cited sub-scores, and rank a
shortlist (or return an explained no-match) — so I get a ranked, explainable shortlist with the
trade-offs surfaced for a human to decide, reproducibly and with no PII leaving the boundary.

## Product invariants referenced

- **AD-002** gates are deterministic + LLM-free (carried; B-2 adds no gate).
- **AD-030** `combined_score = 0.7·skill + 0.3·feedback`, computed in Python from LLM sub-scores.
- **AD-032/033/035** new-joiner skills counted+flagged; adjacency = partial credit + flag, never
  clears a hard skill; the seed adjacency map.
- **AD-040/073** every claim cites a verbatim quote verified present in source; unverifiable
  claims dropped.
- **AD-041/063** no-match over forced match; ordered, capped near-misses.
- **AD-050** single role per `dsm match` invocation (batch loop deferred).
- **AD-071** rerank is a cross-encoder precision stage (Modal `bge-reranker-base`), not the final
  sort key.
- **Golden rule 3 / no-PII-leak** consultant name/email never reach a provider.

## ADRs to ratify at the `T-000-ADR` gate (next free ID: AD-089)

- **AD-089** — Defer query-time dense recall behind `index.recall.enabled` (default `false`); ship
  exhaustive rerank at POC scale; flip ON when the post-filter pool routinely exceeds ~150.
- **AD-090** — Seniority is a soft signal, never a gate; sourced from `CandidateIndexRecord.grade`
  + `years_experience`; **no** `Candidate` amendment.
- **AD-091** — `CandidateStore` port (in `dsm/models.py`) + `GoldCandidateStore` adapter (in
  `dsm/cli/`); move `Grade` → `dsm/models.py`; make `CandidateIndexRecord`/`RetrievedCandidate`
  ingest-free; relocate gold→record projection helpers (`project_filter_fields`/`build_record`/
  `is_indexable`) to the `dsm index` build edge; add the
  `dsm/match/* , dsm/index/* ⊥ dsm/ingest/*` import contract (CLI + build edge exempt).
  **Frozen / shared-contract + cross-lane → requires sign-off.**
- **AD-092** *(ratified at the T-007 score gate)* — Add `FlagType.FRESHNESS_WARNING` so the `warn`
  freshness verdict (AD-087) surfaces as a per-assessment `Flag` (FR-6-AC-5). Frozen-contract
  amendment (AD-060), parallel to AD-088.

---

## Functional requirements (EARS)

### FR-1 — Candidate materialisation via `CandidateStore` (AD-091, §6.0)

- **FR-1-AC-1** — The system SHALL define a `CandidateStore` protocol in `dsm/models.py` with
  `get(candidate_ids: list[str]) -> list[Candidate]`, depended on by `dsm/match` and `dsm/index`
  **by interface only**.
- **FR-1-AC-2** — WHEN the CLI runs a match, it SHALL build a `GoldCandidateStore` adapter (in
  `dsm/cli/`) that reads gold via `goldstore` and hydrates a serving `Candidate`
  (`Location`/availability/`Skill` proficiency from `MergedSkill`, `feedback`, `profile_summary`),
  carrying `candidate_id` as the store key.
- **FR-1-AC-3** — The hydrated `Candidate.email` and `Candidate.name` SHALL be set to the
  pseudonymised `candidate_id` (never the raw name/email): identity is fetched from the vault
  **only at final human-facing rendering** (deferred — see §"PII boundary"). No raw name/email
  ever reaches the score LLM, the embedder, or the reranker.
- **FR-1-AC-4** — A feedback-denied skill (`MergedSkill.demonstrated is False`) SHALL be excluded
  from the hydrated `Candidate.skills` (mirrors AD-081's `skill_set`), so adjacency/score can
  never credit a refuted skill.
- **FR-1-AC-5** — The `dsm/match/* , dsm/index/* ⊥ dsm/ingest/*` import contract SHALL be added to
  `pyproject.toml`; `dsm/cli/*` and the `dsm index` build edge are exempt. `dsm/index/models.py`
  SHALL no longer import `dsm.ingest` (`Grade` moved to `dsm/models.py`; projection helpers moved
  to the build edge).

### FR-2 — Clarify (step 2, §6.2)

- **FR-2-AC-1** — WHEN `role.description` is empty/None, `clarify_role` SHALL return the
  deterministic echo scorecard (current behaviour; partition `required_skills` by depth).
- **FR-2-AC-2** — WHEN `role.description` carries free text, `clarify_role` SHALL refine the
  scorecard via a bounded DSPy typed `Signature` over `PseudonymisedLM` (`temperature=0`),
  capturing constraints in `clarification_notes` and adding/strengthening hard vs desired skills.
- **FR-2-AC-3** — The clarify LLM SHALL NOT invent a new gate or relax an existing one (the
  `co_location_required` flag, `start_date`, and the location come from the parsed role, not the
  LLM).
- **FR-2-AC-4** — WHEN the clarify LLM errors/times out, the system SHALL fall back to the
  deterministic echo scorecard and log a warning; the role is never dropped.
- **FR-2-AC-5** — Clarify SHALL NOT redact `role.description`/`Client` (demand free text is not
  candidate PII, §7).

### FR-3 — Role query passage (§6.6/§6.7, §12 #7)

- **FR-3-AC-1** — The system SHALL provide `build_role_query_passage(scorecard) -> str` in
  `dsm/index/text_builder.py`, built from the scorecard's hard+desired skill names,
  `min_proficiency`-derived seniority, and `clarification_notes` — capability-only, no new
  `TargetProfileScorecard` field.
- **FR-3-AC-2** — The role passage SHALL be **symmetric** to the candidate `build_embed_text`
  skill span (a test asserts the shared skill-span contract); the BGE query instruction prefix is
  applied at embed time via `EmbedClient.embed(mode="query")`.

### FR-4 — Hybrid recall (step 6, §6.6 — DEFERRED, flag OFF)

- **FR-4-AC-1** — The system SHALL implement `hybrid_recall(pool, role_query, store, config)`
  (dense top-N via `embed(mode="query")` + Milvus ⊕ BM25 top-N ⊕ RRF) but gate it on
  `index.recall.enabled` (default `false`).
- **FR-4-AC-2** — WHEN `index.recall.enabled` is `false`, recall SHALL be a passthrough: every
  surviving candidate proceeds with `dense_score`/`bm25_score`/`rrf_score = None`.
- **FR-4-AC-3** — WHEN `index.recall.enabled` is `true`, recall SHALL narrow the pool via the
  fused RRF score (deterministic: `Σ 1/(k + rank_i)`).
- **FR-4-AC-4** — WHEN a store/embed error occurs while recall is ON, the system SHALL fall back to
  the exhaustive (passthrough) path and log a warning, never dropping candidates.

### FR-5 — Rerank (step 7, §6.7)

- **FR-5-AC-1** — The system SHALL provide `rerank(query, candidates, store, embed_client, top_k)`
  that scores each role–candidate pair jointly via `EmbedClient.rerank()` and returns
  `RetrievedCandidate`s ordered by `rerank_score` desc, truncated to `index.rerank.top_k`.
- **FR-5-AC-2** — Rerank SHALL NOT be the final sort key: step 9 (`rank_assessments`) sets the
  shortlist order; `rerank_score` is carried for `explain` lineage only.
- **FR-5-AC-3** — WHEN the reranker raises `EmbedError`, the system SHALL pass the pool through
  **unranked** (`rerank_score=None`, no truncation), log a warning, and let step 9 produce the
  final order.

### FR-6 — Score + combine (step 8, §6.8)

- **FR-6-AC-1** — `score_candidate` SHALL emit, via a bounded DSPy signature over
  `PseudonymisedLM` (`temperature=0`), the sub-scores `skill_match_score`, `feedback_score`,
  `hard_skill_coverage`, `desired_skill_coverage`, a 1–2 sentence `narrative`, and
  `EvidenceCitation`s.
- **FR-6-AC-2** — Python (never the LLM) SHALL compute
  `combined_score = config.weights.skill·skill_match_score + config.weights.feedback·feedback_score`.
- **FR-6-AC-3** — `desired_skill_coverage` SHALL apply adjacency partial credit (AD-033/035): an
  exact desired skill = 1.0, an adjacent skill (via `config.adjacency_map`) = 0.5, else 0;
  computed in Python, not taken from the LLM.
- **FR-6-AC-4** — Every `EvidenceCitation.text` SHALL be verified present verbatim in the
  candidate's source text; an unverifiable citation is dropped (AD-073), never emitted.
- **FR-6-AC-5** — The assessment SHALL carry the applicable flags: `UNVERIFIED_SKILLS` (new
  joiner), `ADJACENCY_USED` (fired **only** when adjacency credit is actually awarded),
  `ROLL_OFF_UNCERTAIN` (low roll-off confidence), `RETENTION_RISK` (retention-requested feedback),
  and the freshness warn-flag when the verdict is `warn`.
- **FR-6-AC-6** — WHEN the score LLM errors on one candidate, the system SHALL log + skip that
  candidate (counted) and continue scoring the rest.
- **FR-6-AC-7** — Hard skills SHALL NEVER be credited via adjacency in any sub-score (AD-033;
  enforced in code regardless of LLM output).

### FR-7 — Orchestrator (full 9-step, §4/§10)

- **FR-7-AC-1** — `dsm match --role-id <id>` SHALL run: parse demand → clarify → freshness →
  gate → exact filter → (recall) → rerank → score → rank, and print a `ShortlistResult` or
  `NoMatchResult` JSON.
- **FR-7-AC-2** — Freshness SHALL run once per CSV banner at the command edge: `refuse` blocks the
  run (non-zero exit, no shortlist); `warn` carries the `FreshnessVerdict` into scoring so every
  assessment gets a freshness flag; `ok` proceeds silently.
- **FR-7-AC-3** — WHEN the pool is empty at any post-gate narrowing stage (gate, exact filter), the
  system SHALL return a `NoMatchResult` with ordered, capped (top-3) near-misses (AD-063b/c/d);
  near-miss ordering includes `HARD_SKILL_MISMATCH` below availability misses (AD-088, from B-1).
- **FR-7-AC-4** — `dsm match` SHALL match exactly **one** role per invocation (selected by
  `--role-id` from the parsed CSV); a batch loop is deferred (AD-050). WHEN `--role-id` matches no
  parsed role, the command SHALL exit non-zero with a clear message.
- **FR-7-AC-5** — Every result (`ShortlistResult`/`NoMatchResult`) SHALL carry the
  `config_snapshot` (weights, `top_k`, model IDs).
- **FR-7-AC-6** — All provider access SHALL go through `PseudonymisedLM`; `temperature=0` on every
  LLM call. `dsm/match` and `dsm/index` SHALL reach the embedder/reranker only through the injected
  `EmbedClient` — never `modal` directly.

### FR-8 — Explain CLI (§9)

- **FR-8-AC-1** — `dsm explain <role_id>` SHALL re-run the pipeline and dump the result's lineage:
  freshness verdict, gate/exact-filter outcomes, recall mode (exhaustive vs hybrid), rerank model +
  scores, sub-scores + combine weights, citations, and `config_snapshot`.
- **FR-8-AC-2** — `explain` SHALL add no new persistence layer — it reads what
  `ShortlistResult`/`NoMatchResult` (+ retrieval provenance) already carry (§9). For a no-match, it
  dumps the reason + ordered near-misses with recomputed gaps.

### FR-9 — Config (§"config")

- **FR-9-AC-1** — `config/default.yaml` SHALL gain `index.recall.enabled: false`,
  `index.recall.top_n: 100`, and `index.rerank.top_k`.
- **FR-9-AC-2** — Weights SHALL stay at top level (`weights.skill`/`weights.feedback`); no
  `scoring.weights.*` path SHALL be introduced.
- **FR-9-AC-3** — `adjacency_map` SHALL be seeded with the AD-035 entries (JVM/Frontend/Cloud/
  Containers/SQL/Data/Test/GenAI/ML).

---

## Non-functional requirements

- **NF-1** — No network or LLM calls in unit tests: the DSPy predictors are injected (mocked like
  `enrich`'s `predict` seam), Milvus runs against a temp in-process Lite `.db`, and the embed/rerank
  client is a `FakeEmbedClient`.
- **NF-2** — Determinism: same Open Roles CSV + same supply + same config + same model versions →
  byte-identical output (LM mocked to fixed output in tests; `temperature=0` in prod).
- **NF-3** — No new dependencies beyond `docs/tech.md`.
- **NF-4** — `make check` GREEN (format, lint, typecheck, all tests, all import contracts —
  including the new `match`/`index` ⊥ `ingest` contract).
- **NF-5** — Hybrid recall stays OFF (`index.recall.enabled = false`): the flip to ON is a config
  change, not a code change. The cross-encoder is the default reranker; the LLM-rerank variant is
  documented, not wired.

---

## Out of scope (deferred)

- Everything B-1 delivered (demand parse, freshness, gates, exact filter, rank) — consumed only.
- Demand-side PII redaction (demand carries no candidate PII, §7).
- An Open Roles CSV fixture under `data/raw/demand/` (§12 #4; synthetic test CSVs in `tmp_path`).
- The LLM-rerank variant (documented; cross-encoder is the default).
- Enabling hybrid recall (flag stays OFF; the stage is fully specified but dormant).
- `FlagType.SKILL_CONFLICT` (§12 #2).
- A `grade` field on the serving `Candidate` (§12 #3; sourced from the index record).
- Wiring `make eval` invariants — a separate Lane-C slice `c-002-query-eval-harness` (§12 #9). B-2
  adds eval *cases* in `design.md`; `make check` green is this slice's DoD.
- The live `PseudonymisedLM` Presidio anonymiser (still a pass-through stub; routing through it
  satisfies the provider-path rule, tests mock the LM).
- Final human-facing identity rendering (vault read path is Lane C; output carries the
  pseudonymised id this slice).
