# Tasks — b-002 Query-Time Scoring + Retrieval + Full Orchestration

> Ordered, atomic, independently testable. **One task = one commit**, imperative, referencing the
> spec/ADR. `make check` green before every commit. No network/LLM in unit tests (mock the seam).
> Each task maps to acceptance criteria from `requirements.md`.

## Gate

- **T-000-ADR — Ratify decisions; STOP for human sign-off** → append to `docs/decision.md`:
  **AD-089** (defer hybrid recall behind `index.recall.enabled=false`; exhaustive rerank at POC
  scale; flip ON above ~150), **AD-090** (seniority is a soft signal, never a gate; sourced from
  `CandidateIndexRecord.grade` + `years_experience`; no `Candidate` amendment), **AD-091**
  (`CandidateStore` port in `dsm/models.py` + `GoldCandidateStore` adapter in `dsm/cli/`; move
  `Grade` → `dsm/models.py`; make index models ingest-free; relocate gold→record projection helpers
  to a `dsm/index/build.py` build edge; add the `match`/`index` ⊥ `ingest` import contract). Pin the
  two design decisions: the hydrated `Candidate.email`/`name` = `candidate_id` (pseudonymised; raw
  identity only at render, deferred), and `MergedSkill.proficiency None` → `BEGINNER` floor.
  **Frozen/shared-contract + cross-lane (AD-091 touches Lane A index records + the build edge) →
  STOP for sign-off before any code.** Commit: `docs(decision): AD-089..091 query scoring + CandidateStore port`.
  _(requirements §ADRs; AD-089/090/091)_

## Foundation — AD-091 moves (kept atomic so `make check` stays green)

- **T-001 — `Grade` + `CandidateStore` home; index models ingest-free; build edge + import contract**
  → move `Grade` to `dsm/models.py`; add `CandidateStore` protocol there; re-export `Grade` from
  `dsm/ingest/models.py`. In `dsm/index/models.py`: import `Grade` from `dsm.models`, drop the
  `dsm.ingest` import, add `RetrievedCandidate`, and **move** `is_indexable`/`project_filter_fields`/
  `build_record` to a new `dsm/index/build.py` (which may import `dsm.ingest`). Update
  `dsm/index/indexer.py` + `dsm/cli/commands.py::index` to import the helpers from `dsm.index.build`.
  Add the `match`/`index` ⊥ `ingest` import contract to `pyproject.toml` with the
  `dsm.index.build -> dsm.ingest` exemption (R-1). Move the relocated-helper tests to
  `tests/index/test_build.py`; add `RetrievedCandidate` + `CandidateStore`/`Grade`-home tests.
  Commit: `refactor(index): CandidateStore port + ingest-free index models per AD-091`.
  _(FR-1-AC-1/5; NF-4)_

## Retrieval precision

- **T-002 — Role query passage + skill-span symmetry** → add `build_role_query_passage(scorecard)`
  to `dsm/index/text_builder.py`; extract the shared `_skill_phrase` helper used by both builders.
  Tests (`tests/index/test_text_builder.py`): deterministic/sorted passage; role/candidate
  skill-span symmetry. Commit: `feat(index): symmetric role query passage builder per §6.6/§6.7`.
  _(FR-3-AC-1/2)_

- **T-003 — Hybrid recall (OFF by default)** → add `hybrid_recall(...)` to `dsm/index/retrieve.py`
  (passthrough when `index.recall.enabled` is false; dense ⊕ BM25 ⊕ RRF when on; error →
  exhaustive fallback) + the additive store read/search helpers on `MilvusIndexStore`. Tests
  (`tests/index/test_retrieve_recall.py`, temp Lite db + `FakeEmbedClient`): OFF passthrough (scores
  None), ON fused scores, RRF determinism, error → fallback. Commit:
  `feat(index): hybrid dense+BM25+RRF recall behind index.recall.enabled per AD-089`.
  _(FR-4-AC-1/2/3/4; NF-5)_

- **T-004 — Rerank (cross-encoder)** → add `rerank(...)` to `dsm/index/retrieve.py`
  (`EmbedClient.rerank`, sort desc, truncate to `index.rerank.top_k`; `EmbedError` → unranked
  passthrough). Tests (`tests/index/test_retrieve_rerank.py`, `FakeEmbedClient`): ordering,
  truncation, error → `rerank_score=None` no truncation. Commit:
  `feat(index): cross-encoder rerank with top_k truncation + error fallback per §6.7`.
  _(FR-5-AC-1/2/3; NF-5)_

## LLM stages

- **T-005 — Config: recall/rerank keys + adjacency seed** → edit `config/default.yaml`: add
  `index.recall.enabled: false`, `index.recall.top_n: 100`, `index.rerank.top_k`; seed
  `adjacency_map` with the AD-035 entries; weights stay top-level. Add `config/prompts/
  role_clarification.md` + `config/prompts/candidate_scoring.md` (versioned signature instructions).
  Commit: `feat(config): recall/rerank keys + adjacency map seed + clarify/score prompts`.
  _(FR-9-AC-1/2/3)_

- **T-006 — Clarify LLM path** → update `dsm/match/clarify.py`: add `ScorecardClarification`
  (match-local DSPy output, in `dsm/match/models.py`), `RoleClarification` signature,
  `make_clarify_predictor`, and the `predict`-seam `clarify_role` (echo when empty/no predictor;
  refine when free text; LLM error → echo + warn; gate fields never LLM-set; no redaction). Tests
  (`tests/match/test_clarify.py`): echo, mocked LLM refine, failure fallback. Commit:
  `feat(match): bounded DSPy clarify over PseudonymisedLM with echo fallback per §6.2`.
  _(FR-2-AC-1..5)_

- **T-007 — Score rewrite: sub-scores + combine + adjacency + flags + citations** → rewrite
  `dsm/match/score.py`: `ScoreExtraction` (match-local DSPy output), `CandidateScoring` signature,
  `make_score_predictor`, the `predict`-seam `score_candidate` (Python combine from `config.weights`;
  exact `hard_skill_coverage` no-adjacency; adjacency partial-credit `desired_skill_coverage`;
  citation verify drop; flags incl. freshness; LLM error → None). Tests (`tests/match/test_score.py`):
  all of FR-6. Commit: `feat(match): LLM sub-scores + deterministic combine + adjacency + cited flags per §6.8`.
  _(FR-6-AC-1..7)_

## Orchestration

- **T-008 — `GoldCandidateStore` adapter** → add `GoldCandidateStore` to `dsm/cli/` (own module
  `dsm/cli/store.py` or in `commands.py`): `get`/`all_ids`, gold→serving-`Candidate` hydration
  (skills exclude `demonstrated is False`; `email`/`name`=`candidate_id`; tombstoned/thin skipped;
  `None` proficiency → BEGINNER). Tests (`tests/cli/test_store.py`, gold fixtures in `tmp_path`).
  Commit: `feat(cli): gold-backed CandidateStore adapter per §6.0/AD-091`. _(FR-1-AC-2/3/4)_

- **T-009 — Full 9-step orchestrator** → rewrite `run_match` (gate → exact filter → recall →
  rerank → score → rank; empty-pool no-match at each narrowing stage; merged exclusion log; extend
  `build_near_misses` for `HARD_SKILL_MISMATCH`; extend `config_snapshot`); rewrite `match`
  (parse → select role → hydrate → freshness refuse/warn → clarify → `run_match`). Tests
  (`tests/cli/test_orchestrator.py`, all seams mocked). Commit:
  `feat(cli): full 9-step query orchestrator with freshness wiring per §4/§10`.
  _(FR-7-AC-1..6)_

- **T-010 — `dsm explain` CLI** → add `explain(role_id)` re-running `run_match` and dumping lineage
  (freshness, gate/exact outcomes, recall mode, rerank scores, sub-scores, citations,
  `config_snapshot`; no-match → reason + near-misses). No new store. Tests (`tests/cli/test_explain.py`).
  Commit: `feat(cli): dsm explain role lineage dump per §9`. _(FR-8-AC-1/2)_

## Close-out

- **T-011 — Verify + handoff** → confirm `make check` GREEN (all tests, all 4 import contracts incl.
  the new `match`/`index` ⊥ `ingest`); confirm no new deps; update `docs/progress.B.md` via
  `/handoff`. (Index refresh `docs/progress.md` happens at merge to `main` via `/handoff-index`.)
  Commit (docs only): `docs(progress): b-002 query scoring + orchestration handoff (Lane B)`.
  _(NF-3/4; DoD)_

## Notes

- **B-1 is a prerequisite** (merged, PR #18): consume `demand.py`/`freshness.py`/`gates.py`/
  `exact_hard_skill_filter`/`rank.py` — do not re-implement.
- **Do not import `dsm/ingest/`** from `dsm/match/` or `dsm/index/` (new contract, T-001). The CLI
  orchestrator and `dsm/index/build.py` are the only exempt edges.
- **All provider access through `PseudonymisedLM`** (clarify, score) and the injected `EmbedClient`
  (recall, rerank) — no direct `modal`/`httpx` from `match`/`index`.
- **Hybrid recall stays OFF** — implemented fully; ON is a config flip, not a code change.
