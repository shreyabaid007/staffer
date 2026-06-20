# Tasks — a-004 Modal Embedder + Reranker Deployment

> Ordered, atomic, independently testable. **One task = one commit**, imperative, referencing the
> spec/ADR. `make check` green before every commit.

## Implementation

- **T-000-ADR — Record AD-080** → append to `docs/decision.md`: add `models.reranker` to
  `config/default.yaml` (tech.md rule 6, config over constants). Also add `models.reranker:
  "BAAI/bge-reranker-base"` to `config/default.yaml`. Own commit
  (`feat(config): add models.reranker per AD-080`). _(CFG-1; AC-5; AC-6)_

- **T-001 — Modal app + container image + StafferModels** → rewrite `modal/embedder.py`:
  `modal.App("staffer-models")`, container image with `sentence-transformers` + `torch` +
  `run_function(download_models)`, `StafferModels` class with `@modal.enter()` (load both models),
  `@modal.method()` `embed(texts, mode)` (768-dim L2-normalized, BGE query instruction prefix for
  mode="query"), `@modal.method()` `rerank(query, passages)` (cross-encoder scores). GPU=T4,
  scaledown_window=300, min_containers=0. Own commit
  (`feat(modal): implement StafferModels embedder + reranker on T4 per AD-074/AD-071`). _(EMB-1;
  EMB-2; RR-1; NF-1; NF-2; NF-3)_

- **T-002 — EmbedClient protocol + ModalEmbedClient** → new `dsm/index/embed_client.py`:
  `EmbedClient` protocol (`embed`, `rerank`), `ModalEmbedClient` implementation using
  `modal.Cls.from_name()` + `.remote()`, `EmbedError` domain exception. Own commit
  (`feat(index): add EmbedClient protocol + Modal implementation per AD-074`). _(CL-1; CL-2; NF-4)_

- **T-003 — Unit tests for embed client** → new `tests/index/test_embed_client.py`: mock the Modal
  `.remote()` boundary. Tests: (a) embed returns correctly shaped vectors, (b) rerank returns float
  scores, (c) Modal errors wrapped in `EmbedError`, (d) mode parameter forwarded correctly. No
  network calls. Own commit (`test(index): embed client unit tests with mocked Modal boundary`).
  _(NF-5; AC-4)_

- **T-004 — Opt-in Modal smoke test** → add a smoke test (gated by `DSM_MODAL_SMOKE=1`) that calls
  the deployed Modal functions with sample capability texts, verifying 768-dim normalized vectors and
  ordered rerank scores. Own commit
  (`test(modal): opt-in smoke test for deployed embedder + reranker`). _(NF-6; AC-2; AC-3)_

- **T-005 — Deploy + verify** → `modal deploy modal/embedder.py`. Verify app in Modal dashboard.
  Run smoke test. No commit (operational step). _(AC-1)_
