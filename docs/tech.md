# Tech Steering — Demand–Supply Matcher

> Always-loaded context. The HOW. Opinionated and pinned. Changes here need an ADR in `docs/decision.md`.

## Architecture in one line
A local Python orchestrator running **structured RAG**: clarify (LLM) → **deterministic gates (Python)** → hybrid retrieve (Milvus) → **rerank (cross-encoder)** → score (LLM) → rank, with a **PII trust boundary** around external calls.

## Pattern: structured RAG, NOT agentic
The LLM is bounded to typed pre/post steps (clarify, score). Retrieval is deterministic. **No agent loops.** Reason: explainability, reproducibility, tractable evals, predictable cost. Do not introduce agentic retrieval without an ADR.

## Stack (pinned — no new deps without an ADR)
- **Runtime:** Python 3.12, `uv` (deps + lockfile), `mise` (tool versions). Reproducible by construction.
- **Data / validation:** Pydantic v2 — every boundary is a typed model.
- **Parsing:** Docling (resumes: PDF + free-form); CSV supply snapshots parsed deterministically — **never an LLM** (AD-065).
- **LLM orchestration:** DSPy — every LLM call is a typed `Signature`. **No raw prompt strings to the provider.**
- **Embeddings:** `BAAI/bge-base-en-v1.5` (sentence-transformers), deployed on **Modal** (serverless GPU, AD-074); capability-only passage, instruction-prefixed asymmetric passage/query formatting (AD-072).
- **Reranker:** `BAAI/bge-reranker-base` (cross-encoder, on Modal alongside the embedder) or a bounded LLM — query-time precision stage (AD-071).
- **Reasoning LLM:** via **OpenRouter** (Claude Sonnet default; swappable through DSPy).
- **PII:** Presidio + spaCy `en_core_web_lg` + deterministic redact-first + outbound leak-scan, **local on the orchestrator**, applied at **every** LLM call site — ingestion `enrich` and query-time `score` — through `pii/PseudonymisedLM` (AD-101). Identity (name/email ↔ `candidate_id`) lives in a **persistent, gitignored vault** read at query time for the deterministic strip (AD-102); it is **file-backed but not yet encrypted** — encryption / retention limits / purge-by-id are deferred to the AD-068 hardening slice.
- **Vector store:** Milvus Lite (embedded), hybrid dense + BM25 + RRF.
- **Ingestion storage:** content-addressed **bronze/silver/gold** layers (local FS → object storage), JSONL→SQLite manifest, content+version derivation cache (AD-066).
- **Eval:** Three-tier pytest harness (AD-095): **Tier 1** code-based invariant evaluators (deterministic, cassette LM, `make check`); **Tier 2** signature regression (`clarify`/`score` shape pinning, `make eval`); **Tier 3** live smoke + cassette drift guard (real LLM, key-gated, `make eval`). No LLM judge for objective invariants. **AI eval layer (AD-104/105/106):** hand-labelled golden set (20–40 cases, draft until human sign-off), DeepEval G-Eval narrative-faithfulness judge (validated against golden labels, adopted only if TPR/TNR ≥ 80%), deterministic Recall@K + contextual precision over retrieval. All non-gating (`make eval` only); the six deterministic invariants remain the commit gate.
- **CLI:** Typer.

## Hard technical rules
1. **PII boundary:** all LLM access via `pii/PseudonymisedLM`, the real anonymiser at **both** call sites — ingestion `enrich` and query-time `score` (AD-101). Per call, identity is redacted **deterministically first** (known identifiers — at query time supplied via a `pii_context` call-context resolved from the vault), residual handled by NER, an outbound **leak-scan** blocks any residual-PII call and fails the build/eval (AD-069), and the per-call placeholder map is **in-memory only**; the response is de-anonymised on the way back. `clarify` carries role text only (no candidate PII) → pass-through. Name/email live in a **persistent vault** keyed by `candidate_id` (AD-102; **encryption deferred** to AD-068). Embedding text excludes `name`/`email` **by construction**. Verified by the `no-PII-leak` eval invariant.
2. **Determinism:** LLM `temperature=0`; fixed seeds; content-hash cache for extractions + embeddings — the cache is the source of truth. Same inputs → same outputs. Ingestion layers (bronze/silver/gold) are immutable + content-addressed; **replay runs from bronze**, and a pinned model version *is* a derivation version — a model bump is a re-extract, never silent drift (AD-066).
3. **Gates are LLM-free:** `match/gates.py` is pure Python over Pydantic models; it must not import `pii/`, `index/`, or any LLM code.
4. **Score combination is deterministic:** the LLM emits sub-scores; **Python** computes `0.7·skill + 0.3·feedback`. Weights from `config/`.
5. **Adjacency enforced in code:** a `hard_depth_skill` is never credited via adjacency, regardless of LLM output. Hard skills are matched structurally via `skill_set`/BM25, **never by cosine similarity** (AD-072).
6. **Config over constants:** weights, adjacency map, availability window, K, model IDs live in `config/`, never inline.

## Coding standards
- Full type hints; `pyright`/`mypy` clean. Pydantic models, not dicts, across boundaries.
- Functions small and pure; side effects (I/O, network, LLM) at the edges only.
- Errors explicit: parse/extraction failures are logged and reported, **never silently swallowed** — the product needs visibility.
- **No network or LLM calls in unit tests** — mock the boundary. Real calls live only in the eval suite.
- Structured logging; never log raw prompts/responses or the pseudonym map.

## Cost & performance
- High-volume work (embed, retrieve, rerank, NER) is local/Modal → ~0 marginal cost. Only **three** bounded LLM steps hit OpenRouter (enrich, clarify, score); the default reranker is a Modal-hosted cross-encoder, so an optional LLM rerank would be a *fourth* (AD-071).
- Batch embeds in one Modal call. Modal credits are ample at this scale.

## Deferred (revisit only via ADR)
Open-weights LLM on Modal · Milvus server · streaming refresh · agentic retrieval.
