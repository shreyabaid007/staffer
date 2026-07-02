# c-011 — Web supply ingest (tasks)

One task = one commit, imperative, referencing this spec.

- [ ] **T-000 · ADRs** — Append `docs/decision.md`: **AD-XXX** (incremental ingest correctness:
  full-corpus merge + `FileEnrichCache` + gold write gate; fixes G-1/G-2) and **AD-XXY**
  (web supply ingest API + one-button pipeline trigger; subprocess job runner; supply CSVs as
  the editable source of truth). Placeholders — `/handoff-index` assigns real numbers at merge.
  *(maps: FR-5, FR-4)*

- [ ] **T-001 · enrich cache** — `dsm/ingest/enrich_cache.py` (`FileEnrichCache`) +
  `read_records` in `dsm/ingest/blobstore.py` + `tests/ingest/test_enrich_cache.py`.
  *(FR-5-AC-2 key/atomicity; FR-5-AC-1 bronze read-back)*

- [ ] **T-002 · ingest incrementality** — rewire `dsm/cli/commands.py::ingest`: process
  LANDED+SKIPPED entries via persisted bronze, wire the cache around `enrich_resume`/
  `enrich_feedback` (guard → cache → LLM), gold write gate + updated/unchanged summary counts,
  keep reconcile/revive/leak-abort semantics. `tests/cli/test_ingest_incremental.py` two-run
  scenario. *(FR-5-AC-1..5)*

- [ ] **T-003 · supply read API** — DTOs + `GET /supply` (CSV parse, status join via
  `commands.derive_candidate_id` + gold/silver), `tests/web/test_supply.py::read`.
  *(FR-1-AC-1..3, NF-1)*

- [ ] **T-004 · supply mutate API** — `POST /supply/candidates`, `DELETE
  /supply/candidates/{category}/{email}` with banner bump + atomic CSV write; tests.
  *(FR-2-AC-1..4)*

- [ ] **T-005 · attachments API** — resume upload/delete with `link_check`; feedback
  write/upload/delete with guaranteed-link Markdown assembly; traversal guard;
  `tests/web/test_attachments.py`. *(FR-3-AC-1..4)*

- [ ] **T-006 · ingest job API** — single-flight background runner (`POST /ingest/run`,
  `GET /ingest/status`), summary parser, `tests/web/test_ingest_job.py`. *(FR-4-AC-1..3)*

- [ ] **T-007 · frontend** — two-tab `static/index.html`: supply table (grouped sections,
  inline add, per-row resume/feedback actions, remove, filter, status chips), Run-ingest
  button + polling + incremental summary; match tab preserved. *(FR-6-AC-1..4)*

- [ ] **T-008 · verify end-to-end** — `dsm serve` on real `data/`: add a candidate + resume +
  feedback via the UI, run ingest, confirm 0 LLM calls for unchanged docs (cache hits in
  log), index `skipped_unchanged` ≈ pool size, new candidate matchable; remove them, run,
  confirm tombstone + index delete. Record result in the lane file. *(FR-5-AC-4 live)*

- [ ] **T-009 · handoff** — `make check` green; `/handoff` lane update; backlog note for the
  provenance-hash re-embed bound (design §A.4).
