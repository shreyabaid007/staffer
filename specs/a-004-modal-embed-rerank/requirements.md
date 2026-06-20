# Requirements — a-004 Modal Embedder + Reranker Deployment

> Slice 4 of ingestion/retrieval: deploy the BGE embedder and cross-encoder reranker to Modal
> serverless GPU, plus the typed client that `dsm/index/` uses to call them. This is the
> infrastructure foundation for real vector retrieval (replacing `dsm/index/stub.py`).
> References: `ee-ingestion-architecture.md` §8 (index & embedding), §14 (tech stack);
> AD-051/AD-071/AD-072/AD-074; `docs/tech.md` rules 1/6; `config/default.yaml` models section.

## User story

As the staffing engine's index subsystem, I need the embedding and reranking models deployed to
Modal and callable via a typed Python client, so that the retrieval pipeline can embed gold candidate
profiles and rerank query-time results using the same models that will serve production — without
any local GPU requirement and with zero-download cold starts.

## Functional requirements

- **EMB-1:** A Modal function accepts a batch of text passages and returns dense 768-dim L2-normalized
  vectors using `BAAI/bge-base-en-v1.5`.
- **EMB-2:** The embed function supports asymmetric formatting — `mode="passage"` for indexing
  (no prefix) and `mode="query"` for retrieval (instruction-prefixed per BGE docs).
- **RR-1:** A Modal function accepts a query and a list of passages and returns relevance scores
  using the `BAAI/bge-reranker-base` cross-encoder.
- **CL-1:** A typed `EmbedClient` protocol in `dsm/index/` abstracts the Modal boundary. The
  protocol is injectable/mockable for unit tests (no network calls in `make check`).
- **CL-2:** A `ModalEmbedClient` implementation calls the deployed Modal app via `.remote()`.
- **CFG-1:** The reranker model ID is added to `config/default.yaml` alongside the existing embedder
  entry, following the "config over constants" rule (tech.md rule 6).

## Non-functional requirements

- **NF-1:** Model weights are baked into the Modal container image (no download at cold start).
  Cold start target: < 30 seconds.
- **NF-2:** GPU tier is T4 (both models combined < 2GB VRAM; T4 has 16GB).
- **NF-3:** No always-on container cost — `scaledown_window=300` (5 min), `min_containers=0`.
- **NF-4:** Import contracts remain passing — `dsm.index` may import `modal`; `dsm.ingest` and
  `dsm.match` remain forbidden from `modal`.
- **NF-5:** `make check` stays green. No network calls in unit tests.
- **NF-6:** An opt-in smoke test (`DSM_MODAL_SMOKE=1`) verifies the deployed functions end-to-end.

## Acceptance criteria

| ID | Criterion |
|---|---|
| AC-1 | `modal deploy modal/embedder.py` succeeds and the app appears in the Modal dashboard |
| AC-2 | Smoke test: embed 3 capability passages → 3 vectors of shape (768,), L2-normalized |
| AC-3 | Smoke test: rerank a query against 3 passages → 3 float scores, higher = more relevant |
| AC-4 | `make check` green (existing 240+ tests + new unit tests, import contracts pass) |
| AC-5 | `config/default.yaml` has `models.reranker` entry |
| AC-6 | AD-080 recorded in `docs/decision.md` |

## Out of scope

- Building `embed_text` from `GoldCandidate` (next spec: index/retrieval)
- Milvus integration (next spec)
- Wiring retrieval into `dsm match` (next spec)
- Model swaps / benchmarking (deferred per AD-074; architecture is model-agnostic)
