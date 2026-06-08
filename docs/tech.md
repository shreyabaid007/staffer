# Tech Steering — Demand–Supply Matcher

> Always-loaded context. The HOW. Opinionated and pinned. Changes here need an ADR in `docs/decision.md`.

## Architecture in one line
A local Python orchestrator running **structured RAG**: clarify (LLM) → **deterministic gates (Python)** → hybrid retrieve (Milvus) → score (LLM) → rank, with a **PII trust boundary** around external calls.

## Pattern: structured RAG, NOT agentic
The LLM is bounded to typed pre/post steps (clarify, score). Retrieval is deterministic. **No agent loops.** Reason: explainability, reproducibility, tractable evals, predictable cost. Do not introduce agentic retrieval without an ADR.

## Stack (pinned — no new deps without an ADR)
- **Runtime:** Python 3.12, `uv` (deps + lockfile), `mise` (tool versions). Reproducible by construction.
- **Data / validation:** Pydantic v2 — every boundary is a typed model.
- **Parsing:** Docling (profiles: PDF + free-form).
- **LLM orchestration:** DSPy — every LLM call is a typed `Signature`. **No raw prompt strings to the provider.**
- **Embeddings:** `BAAI/bge-base-en-v1.5` (sentence-transformers), deployed on **Modal** (serverless GPU).
- **Reasoning LLM:** via **OpenRouter** (Claude Sonnet default; swappable through DSPy).
- **PII:** Presidio + spaCy `en_core_web_lg`, **local on the orchestrator**.
- **Vector store:** Milvus Lite (embedded), hybrid dense + BM25 + RRF.
- **Eval:** Promptfoo (signature-level) + DeepEval (end-to-end + invariants).
- **CLI:** Typer.

## Hard technical rules
1. **PII boundary:** all LLM access via `pii/PseudonymisedLM`; mapping in-memory only. Embedding text excludes `name`/`email` **by construction**. Verified by an eval invariant.
2. **Determinism:** LLM `temperature=0`; fixed seeds; content-hash cache for extractions + embeddings. Same inputs → same outputs.
3. **Gates are LLM-free:** `match/gates.py` is pure Python over Pydantic models; it must not import `pii/`, `index/`, or any LLM code.
4. **Score combination is deterministic:** the LLM emits sub-scores; **Python** computes `0.7·skill + 0.3·feedback`. Weights from `config/`.
5. **Adjacency enforced in code:** a `hard_depth_skill` is never credited via adjacency, regardless of LLM output.
6. **Config over constants:** weights, adjacency map, availability window, K, model IDs live in `config/`, never inline.

## Coding standards
- Full type hints; `pyright`/`mypy` clean. Pydantic models, not dicts, across boundaries.
- Functions small and pure; side effects (I/O, network, LLM) at the edges only.
- Errors explicit: parse/extraction failures are logged and reported, **never silently swallowed** — the product needs visibility.
- **No network or LLM calls in unit tests** — mock the boundary. Real calls live only in the eval suite.
- Structured logging; never log raw prompts/responses or the pseudonym map.

## Cost & performance
- High-volume work (embed, retrieve, NER) is local/Modal → ~0 marginal cost. Only **three** bounded LLM steps hit OpenRouter (enrich, clarify, score).
- Batch embeds in one Modal call. Modal credits are ample at this scale.

## Deferred (revisit only via ADR)
Open-weights LLM on Modal · Milvus server · streaming refresh · agentic retrieval.
