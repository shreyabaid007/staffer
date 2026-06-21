# Tasks — a-005 Index BUILD + UPSERT

> Ordered, atomic, independently testable. **One task = one commit**, imperative, referencing the
> spec/ADR. `make check` green before every commit. No network in unit tests.

## Implementation

- **T-000-ADR — Record decisions + foundation** → append to `docs/decision.md`: **AD-081**
  (`skill_set` excludes feedback-denied skills — `demonstrated is False` dropped so an exact/BM25
  hard-skill match never credits a refuted skill), **AD-082** (no embed cache — each Milvus record
  stores `gold_hash` **and** `model_version` = `config models.embedder`; re-embed only when either
  differs, AD-066), **AD-083** (add the `milvus-lite` embedded engine — peer of the already-declared
  `pymilvus`, completing the sanctioned Milvus Lite store, `tech.md` §11), and **AD-084** (no outbound
  known-PII scan at index time — `embed_text` PII-free **by construction**; supersedes the brief's
  `assert_no_leak`; generic NER/org-dictionary outbound scan deferred to Lane C). In the same commit:
  add the `index` block to `config/default.yaml`; add `milvus-lite` to `pyproject.toml` dependencies +
  run `uv lock`; gitignore `data/index/*` (+ `data/index/.gitkeep`). Commit:
  `docs(decision): AD-081..084 + index config + milvus-lite dep`. _(NF-4; NF-5; AC-9; CFG)_

- **T-001 — `CandidateIndexRecord` + projection + `IndexMetrics`** → new `dsm/index/models.py`:
  the frozen `CandidateIndexRecord` (import `Grade` from `dsm.ingest.models`; `Location`/
  `AvailabilityState` from `dsm.models`), `is_indexable`, `project_filter_fields`, `build_record`,
  and `IndexMetrics` (+ `assert_clean`). Tests (`tests/index/test_index_models.py`): filter projection
  per availability variant + Remote-India `city=None`; `is_indexable` False on missing
  grade/location/availability. Commit: `feat(index): CandidateIndexRecord + gold projection per §6 Phase 6`.
  _(IDX-1; IDX-8; AC-3)_

- **T-002 — `embed_text` builder (PII-free by construction)** → new `dsm/index/text_builder.py`:
  `included_skills` (the `demonstrated is not False` predicate) + `build_embed_text` (deterministic,
  sorted; domains prefix + skill phrases w/ proficiency + project descriptions; excludes denied
  skills; reads **only** capability fields — never identity/vault refs, AD-084). Tests
  (`tests/index/test_text_builder.py`): PII-free by construction (gold with vault refs set → neither
  ref nor identity in output), excludes `demonstrated is False`, byte-identical for identical gold,
  order-insensitive to input skill order. Commit:
  `feat(index): deterministic PII-free-by-construction embed_text builder per AD-072/AD-084`.
  _(IDX-2; PII-1; AC-2; AC-6)_

- **T-003 — `skill_set` builder** → add `build_skill_set` to `dsm/index/text_builder.py`
  (`sorted({s.name for s in included_skills(gold)})`). Tests: excludes `demonstrated is False`;
  includes `True`/`None`; sorted + deduped. Commit:
  `feat(index): skill_set excludes feedback-denied skills per AD-081`. _(IDX-3; AC-1)_

- **T-004 — Milvus Lite store** → new `dsm/index/milvus_store.py`: `MilvusIndexStore` with
  `ensure_collection` (dense `FLOAT_VECTOR` dim 768 IP + `skill_set` ARRAY + `skill_text` analyzer +
  BM25 `Function` → `sparse` + scalar/`gold_hash`/`model_version` fields), `upsert`, `delete`,
  `fetch_versions`. Tests (`tests/index/test_milvus_store.py`, tmp `milvus.db`, in-process): upsert
  idempotent (re-upsert ⇒ one entity, dense dim 768); delete removes an id; `fetch_versions` returns
  stored `(gold_hash, model_version)`; insert succeeds without supplying `sparse` (BM25 auto). Commit:
  `feat(index): Milvus Lite candidates collection with BM25 sparse per §8`. _(IDX-5; AC-4)_

- **T-005 — Indexer orchestration** → new `dsm/index/indexer.py`: `index_gold` (read gold →
  tombstone-delete → thin-skip → `(gold_hash, model_version)` gate → `embed(mode="passage")` → upsert;
  return `IndexMetrics`). Takes an injected `EmbedClient` (no `known_pii` — AD-084). Tests
  (`tests/index/test_indexer.py`, `FakeEmbedClient` + tmp store): first run indexes; identical re-run
  ⇒ all `skipped_unchanged` (Fake records **no** new embed); `model_version` bump ⇒ re-embedded;
  tombstone ⇒ delete + `tombstoned_removed`; thin gold ⇒ `thin_skipped`. Commit:
  `feat(index): gold→embed→upsert indexer with (gold_hash,model_version) gating per AD-082`.
  _(IDX-4; IDX-6; IDX-7; AC-5)_

- **T-006 — CLI `dsm index` + summary** → edit `dsm/cli/commands.py` (add `index`) and
  `dsm/cli/main.py` (register the command). Prints the PII-safe `── Index ──` summary (indexed /
  skipped-unchanged / tombstoned-removed / thin-skipped); per-candidate lines = `candidate_id` token +
  structured fields. No bronze read / `DSM_CANDIDATE_ID_KEY` guard (AD-084). Tests
  (`tests/index/test_cli_index.py`): monkeypatch `ModalEmbedClient` → `FakeEmbedClient`, seed a tmp
  gold dir, assert summary text + exit code 0. Commit:
  `feat(cli): dsm index command with PII-safe summary per IDX-9`. _(IDX-9; AC-7)_

- **T-007 — Verify + handoff** → confirm `make check` green (existing + new tests, 3 import contracts,
  no network); confirm `docs/decision.md` carries AD-081/082/083/084 and `config`/`gitignore`/
  `pyproject` are consistent; update `docs/progress.A.md` via `/handoff`. Note remaining scope for
  a-006 (query-time hybrid retrieve + rerank, `dsm match` wiring, chaining index onto `dsm ingest`,
  Lane C's generic outbound NER scan). No code commit beyond doc/handoff. Commit:
  `docs(progress): a-005 index upsert handoff`. _(AC-8; DoD)_

## Definition of Done

Spec acceptance criteria (AC-1…AC-9) met · `make check` green (`gates ⊥ index`, `ingest ⊥ index/modal`
still pass; **no new contract**) · new behaviour tested with `FakeEmbedClient` + tmp Milvus Lite,
no network · AD-081/082/083/084 in `docs/decision.md` · `docs/progress.A.md` updated via `/handoff` ·
`dsm/index/stub.py` + `dsm match` untouched (a-006 owns query-time).
