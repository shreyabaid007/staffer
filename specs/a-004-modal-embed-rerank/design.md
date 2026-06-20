# Design — a-004 Modal Embedder + Reranker Deployment

> Technical design for deploying BGE embedder + cross-encoder reranker to Modal, plus the typed
> client in `dsm/index/`. References: `requirements.md` (this folder); `ee-ingestion-architecture.md`
> §8/§14; AD-051/AD-071/AD-072/AD-074; `docs/tech.md` rules 1/6; Modal SDK 1.3.x.

## Architecture

```
dsm/index/embed_client.py          modal/embedder.py (Modal cloud)
┌─────────────────────────┐        ┌──────────────────────────────┐
│ EmbedClient (Protocol)  │        │ modal.App("staffer-models")  │
│  .embed(texts, mode)    │───────▶│  StafferModels               │
│  .rerank(query, docs)   │ .remote│    .embed(texts, mode)       │
│                         │        │    .rerank(query, passages)   │
│ ModalEmbedClient        │◀───────│                              │
│  (implements Protocol)  │ result │  Image: sentence-transformers│
└─────────────────────────┘        │  GPU: T4                     │
                                   └──────────────────────────────┘
```

## Modal app design (`modal/embedder.py`)

### Container image

```python
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("sentence-transformers", "torch")
    .run_function(download_models)  # bakes weights into image layer
)
```

`download_models()` pre-downloads both `BAAI/bge-base-en-v1.5` and `BAAI/bge-reranker-base` into
the image filesystem. This eliminates network latency at cold start.

### Class design

A single `@app.cls()` class `StafferModels` with:

- **`@modal.enter()`** — loads both models into GPU memory once per container lifecycle.
  - `SentenceTransformer("BAAI/bge-base-en-v1.5")` for embeddings
  - `CrossEncoder("BAAI/bge-reranker-base")` for reranking
- **`@modal.method()` `embed(texts, mode)`** — encodes texts with optional query instruction prefix.
  Returns `list[list[float]]` (768-dim, L2-normalized).
- **`@modal.method()` `rerank(query, passages)`** — scores query-passage pairs.
  Returns `list[float]` (raw logits, higher = more relevant).

### GPU and scaling

- `gpu="T4"` — cheapest tier, 16GB VRAM, ample for both models (~2GB combined).
- `scaledown_window=300` — container stays warm 5 min after last call.
- `min_containers=0` — no always-on cost.

### Model ID duplication

The Modal container cannot read `config/default.yaml` at build time. Model IDs are constants in
`modal/embedder.py` with a comment referencing the config as source of truth. This is a known,
accepted duplication for the Modal boundary.

## Client design (`dsm/index/embed_client.py`)

### Protocol

```python
class EmbedClient(Protocol):
    def embed(self, texts: list[str], *, mode: str = "passage") -> list[list[float]]: ...
    def rerank(self, query: str, passages: list[str]) -> list[float]: ...
```

Follows the injectable-seam pattern from `dsm/ingest/enrich.py` (the `predict` callable).

### Modal implementation

```python
class ModalEmbedClient:
    def __init__(self) -> None:
        self._cls = modal.Cls.from_name("staffer-models", "StafferModels")

    def embed(self, texts: list[str], *, mode: str = "passage") -> list[list[float]]:
        return self._cls().embed.remote(texts, mode)

    def rerank(self, query: str, passages: list[str]) -> list[float]:
        return self._cls().rerank.remote(query, passages)
```

`modal.Cls.from_name()` is a lazy reference — no connection until the first `.remote()` call.

### Error handling

Modal exceptions are caught and wrapped in a domain-specific `EmbedError` so callers don't
depend on the Modal SDK directly.

## Config change (`config/default.yaml`)

```yaml
models:
  reasoning_llm: "anthropic/claude-sonnet-4-6"
  embedder: "BAAI/bge-base-en-v1.5"
  reranker: "BAAI/bge-reranker-base"   # AD-071/AD-080
```

## Modules touched

| Module | New/Edit | Responsibility | Owner |
|---|---|---|---|
| `modal/embedder.py` | **rewrite** | Modal App + StafferModels (embed + rerank) | Lane A |
| `dsm/index/embed_client.py` | **new** | EmbedClient protocol + ModalEmbedClient | Lane A |
| `config/default.yaml` | **edit** | add `models.reranker` | Lane A |
| `docs/decision.md` | **edit** | append AD-080 | Lane A |
| `tests/index/test_embed_client.py` | **new** | unit tests (mocked Modal) | Lane A |

## Testing strategy

- **Unit tests** (`tests/index/test_embed_client.py`): mock the Modal `.remote()` boundary. Verify
  the client passes arguments correctly, wraps errors, and returns typed results. No network.
- **Opt-in smoke test** (`DSM_MODAL_SMOKE=1`): calls the deployed Modal functions with real texts.
  Verifies vector dimensions, normalization, and score ordering. Skipped by default in `make check`.
