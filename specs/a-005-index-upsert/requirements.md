# Requirements — a-005 Index BUILD + UPSERT

> Slice 5 of ingestion/retrieval: turn canonical `GoldCandidate`s into PII-free
> `CandidateIndexRecord`s, embed the capability passage via the **already-merged** a-004
> `EmbedClient`, and upsert into **Milvus Lite**. This is the **write-time** half of retrieval;
> query-time hybrid recall + rerank are the next slice (a-006).
> References: `ee-ingestion-architecture.md` §6 (Phase 6), §8 (index & embedding); `docs/tech.md`
> rules 1/2/5/6; AD-066/AD-072/AD-074; the merged `specs/a-004-modal-embed-rerank/` +
> `dsm/index/embed_client.py`.

## User story

As the staffing engine's index subsystem, I need each canonical gold consultant turned into a
searchable Milvus record — a capability-only dense vector plus the structured hard-skill and filter
fields — written once and re-embedded only when the data or the embedding model changes, so that the
query side (a-006) can do hybrid recall over a current, PII-free index without re-embedding on every
run.

## Functional requirements

- **IDX-1 — Projection.** Build a typed `CandidateIndexRecord` from a `GoldCandidate`: filter
  fields (`grade`, `city`, `remote_eligible`, `availability_type`, `availability_date`,
  `valid_as_of`) come from the `Sourced[...].value` of `gold.grade`/`gold.location`/
  `gold.availability`; `gold_hash` and `model_version` are carried for change-detection.
- **IDX-2 — `embed_text`.** A capability-only, PII-free, **deterministic** passage built from gold:
  a one-line contextual prefix (domains; seniority *evidence* carried via project descriptions) +
  skill phrases with proficiency + domains + project descriptions. Sorted/stable so identical gold
  yields identical text. **Excludes** name/email/client-org/`candidate_id`, availability,
  grade-as-a-label, and **denied (negated) skills** (AD-072).
- **IDX-3 — `skill_set` excludes feedback-denied skills.** `skill_set = [s.name for s in gold.skills
  if s.demonstrated is not False]` — a feedback-refuted skill (`demonstrated is False`) is excluded
  so a later exact hard-skill filter / BM25 match can never credit a refuted skill. **Needs an ADR
  (AD-081).**
- **IDX-4 — Embed (passage mode).** The dense vector is produced by `EmbedClient.embed([embed_text],
  mode="passage")[0]` — 768-dim, L2-normalized (a-004 guarantees this). The indexer depends on the
  **`EmbedClient` protocol**; production injects `ModalEmbedClient`, tests inject a `FakeEmbedClient`.
  `rerank()` is query-time and is **not** used here.
- **IDX-5 — Milvus Lite store.** A collection `candidates` (db at `data/index/milvus.db`): PK
  `candidate_id`; dense `FLOAT_VECTOR` dim 768, metric **IP**; `skill_set` as `ARRAY<VARCHAR>` (exact
  hard-skill filter, AD-072) **and** a BM25 sparse vector over the same skills (`skill_text` VARCHAR
  with analyzer → `sparse` `SPARSE_FLOAT_VECTOR` via a BM25 `Function`); scalar filter fields +
  `embed_text` + `gold_hash` + `model_version`. Operations: `ensure_collection` / `upsert` / `delete`
  — **idempotent** (re-upserting the same record is a no-op in effect).
- **IDX-6 — `(gold_hash, model_version)` gates re-embed.** **No embed cache** (AD-066): each Milvus
  record stores `gold_hash` **and** `model_version` (= `config models.embedder`). On a run, a
  candidate is **re-embedded + upserted only when its stored `(gold_hash, model_version)` do not
  BOTH match** the current pair. A data change flips `gold_hash`; an embedder swap flips
  `model_version` (which `gold_hash` alone would miss). **Needs an ADR (AD-082).**
- **IDX-7 — Tombstones delete, never upsert.** `gold.is_tombstoned is True` → delete that
  `candidate_id` from the collection (idempotent if already absent); never embed or upsert it.
- **IDX-8 — Thin profiles skipped.** A gold entity missing a required filter field
  (`grade`/`location`/`availability` is `None`) is **skipped from the index + logged** (not guessed),
  and counted as `thin-skipped`. (This is distinct from the lineage "thin" *coverage* class, which is
  a CSV-only-but-complete profile that **is** indexable.)
- **IDX-9 — CLI summary.** `dsm index` prints a PII-safe `── Index ──` summary —
  `indexed` / `skipped-unchanged` / `tombstoned-removed` / `thin-skipped`. Per-candidate lines show
  the `candidate_id` token + structured fields only (never name/email/`embed_text`).

## PII requirements (non-negotiable — `docs/tech.md` rule 1, AD-072; AD-084)

- **PII-1 — `embed_text` is PII-free by construction.** The builder reads **only** capability fields
  of gold — `skills` (name/proficiency, denied excluded), `domains[].value`, `projects` — and **never**
  reads `name_vault_ref`/`email_vault_ref`/`candidate_id` or any identity field, and never includes
  availability or grade-as-a-label (AD-072). Identity therefore cannot enter `embed_text` by
  construction; it is asserted by test (AC-6).
- **PII-2 — No outbound known-PII scan at index time (AD-084, supersedes the brief).** The brief's
  "STILL run `assert_no_leak` on `embed_text`" is **overridden**: a known-string scan needs the
  candidate's own name/email, which buys only a narrow backstop (the de-anon-into-`projects` window
  enrich never re-scans) at the cost of coupling `dsm index` to bronze (gold holds vault refs only;
  `InMemoryVault` is non-persistent). Decision: rely on PII-1's construction guarantee; the indexer
  takes **no `known_pii`**, and `dsm index` needs neither bronze nor `DSM_CANDIDATE_ID_KEY`. A
  **generic** outbound NER/org-dictionary scan (which catches client-org names too, without needing
  per-candidate identity) is **deferred to Lane C**, and `build_embed_text` is the seam for it.

## Non-functional requirements

- **NF-1 — No network in unit tests.** Tests inject a `FakeEmbedClient` (implements the protocol,
  returns deterministic 768-dim L2-normalized vectors) and use a **temp Milvus Lite db, in-process**.
  `make check` makes zero network calls.
- **NF-2 — Import contracts stay green.** The three existing contracts hold unchanged: `gates ⊥
  {pii,index,dspy,modal,httpx}`; `{match,ingest} ⊥ {modal,httpx}`; `ingest ⊥ {match,index}`. All new
  code lives in `dsm/index/`, which may import `dsm.pii`, `dsm.ingest.goldstore`/`dsm.ingest.models`,
  `pymilvus`, and `modal` (settled in a-004). **No new contract is required.**
- **NF-3 — Determinism / idempotency.** No embed cache; the Milvus record's `(gold_hash,
  model_version)` is the source of truth for "needs re-embed". Same inputs + same models → no
  re-embed, no churn.
- **NF-4 — `milvus-lite` dependency.** The embedded engine `milvus-lite` is added to `pyproject.toml`
  + `uv.lock`. `pymilvus` alone cannot open a local `.db`. Milvus Lite is the sanctioned vector store
  (`tech.md` §11) — this completes that choice rather than introducing a new technology. **Needs an
  ADR (AD-083).**
- **NF-5 — `data/index/` gitignored.** The Milvus db carries derived (PII-free) vectors + metadata;
  it is a rebuildable artifact and is not committed. `data/index/*` ignored with a `.gitkeep`.

## Acceptance criteria

| ID | Criterion |
|---|---|
| AC-1 | `skill_set`/`embed_text` exclude a skill with `demonstrated is False`; include `True`/`None` (AD-081) |
| AC-2 | `embed_text` contains no `name`/`email`; is deterministic + sorted (identical gold → identical text) |
| AC-3 | Projection maps grade/city/remote/availability_type/availability_date/valid_as_of from gold; thin profile (grade/location/availability None) → skipped + counted (IDX-8) |
| AC-4 | Milvus collection: dense `FLOAT_VECTOR` dim **768**, metric IP, PK `candidate_id`, BM25 sparse over skill text; upsert is idempotent; delete removes a tombstoned id |
| AC-5 | `(gold_hash, model_version)` gating: unchanged candidate → **not** re-embedded; an embedder-id bump → re-embedded (AD-082) |
| AC-6 | `embed_text`/`skill_set` builders consume only capability fields — given gold with vault refs set, the output contains neither ref and no identity (PII-free by construction, AD-084) |
| AC-7 | `dsm index` prints the `── Index ──` summary (indexed/skipped-unchanged/tombstoned-removed/thin-skipped), PII-safe |
| AC-8 | `make check` green — existing tests + new FakeEmbedClient/tmp-Milvus tests; 3 import contracts pass; no network |
| AC-9 | AD-081 / AD-082 / AD-083 / AD-084 recorded in `docs/decision.md`; `index` config block + `milvus-lite` dep present |

## Decisions — all signed off

1. **`skill_set` excludes feedback-denied skills** (`demonstrated is False`) → AD-081. ✅
2. **No embed cache** — `(gold_hash, model_version)` on the Milvus record gates re-embed; `model_version`
   = `config models.embedder` (the embedder id) → AD-082. ✅
3. **Thin profiles skipped** from the index (grade/location/availability `None`) — logged, not guessed
   (IDX-8). ✅
4. **Query-time retrieval + rerank deferred to a-006** — this slice is write-time only;
   `dsm/index/stub.py` and `dsm match` are untouched. ✅
5. **Add `milvus-lite`** (embedded-engine peer of `pymilvus`; not in the lock) → AD-083. ✅ (Milvus Lite
   is the sanctioned store, `tech.md` §11; this completes it.)
6. **Build the BM25 sparse field now** (verified working on Milvus Lite, offline) so a-006 only adds
   query logic and never needs a re-index. ✅
7. **No outbound known-PII scan at index time** → AD-084. ✅ Rely on `embed_text` PII-free **by
   construction** (PII-1); the indexer takes no `known_pii`; a generic NER/org-dictionary outbound scan
   is Lane C's later hardening. **This supersedes the brief's "STILL run `assert_no_leak`."**

## Out of scope (a-006 / later)

- Query-time hybrid retrieve (dense + BM25 + RRF), the `mode="query"` embed, and `rerank()` wiring
  into `dsm match` — `dsm/index/stub.py` stays in place this slice.
- Chaining the index phase onto `dsm ingest` (standalone `dsm index` is delivered; the chain is a
  small follow-on).
- **Generic outbound NER/org-dictionary scan over `embed_text`** (catches client-org names without
  per-candidate identity) — Lane C hardening; `build_embed_text` is left as the seam (AD-084).
- Encrypted at-rest / persistent vault read path (Lane C).
- Model swaps / benchmarking (AD-074); Milvus *server* (Lite only).
