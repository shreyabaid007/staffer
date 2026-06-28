<p align="center">
  <h1 align="center">Staffer</h1>
  <p align="center">
    <strong>AI-powered staffing decision engine</strong>
    <br />
    Ranked, explainable consultant shortlists — with trade-offs surfaced for humans to decide.
  </p>
  <p align="center">
    <a href="#quick-start">Quick Start</a> &middot;
    <a href="#how-it-works">How It Works</a> &middot;
    <a href="#usage">Usage</a> &middot;
    <a href="docs/product.md">Product Spec</a> &middot;
    <a href="docs/tech.md">Tech Spec</a>
  </p>
</p>

---

## What is Staffer?

Staffer is a **structured RAG pipeline** that matches open roles to consultants. Given a role description, it returns a ranked shortlist of candidates with per-candidate explanations, skill gap analysis, and confidence scores — all with a strict PII boundary that ensures no personal data ever reaches external LLMs.

**Key design principles:**

- **Deterministic gates** — Location and availability filtering is pure Python. An LLM can never override eligibility.
- **Explainable output** — Every claim in the shortlist cites real source evidence (resume excerpts, feedback quotes). No hallucinated justifications.
- **PII-safe by construction** — All LLM calls route through a `PseudonymisedLM` boundary with redact-first + leak-scan gates. Import-linter contracts enforce this at build time.
- **Two-tier scoring** — Hard skills are matched structurally (exact + BM25); soft signals (seniority, feedback) go through bounded LLM scoring with configurable weights.

## How It Works

```
                        ┌─────────────────────────────┐
                        │        Open Role (CSV)       │
                        └──────────────┬──────────────┘
                                       │
                              ┌────────▼────────┐
                              │  1. Parse Demand │
                              └────────┬────────┘
                                       │
                    ┌──────────────────▼──────────────────┐
                    │  2. Deterministic Gates              │
                    │     Location match + Availability    │
                    │     (pure Python — no LLM)           │
                    └──────────────────┬──────────────────┘
                                       │
                    ┌──────────────────▼──────────────────┐
                    │  3. Hybrid Retrieval                 │
                    │     Hard-skill filter → Dense+BM25   │
                    │     → RRF fusion → Cross-encoder     │
                    │       rerank (Modal GPU)             │
                    └──────────────────┬──────────────────┘
                                       │
                    ┌──────────────────▼──────────────────┐
                    │  4. LLM Scoring (via PseudonymisedLM)│
                    │     Skill match · Feedback · Seniority│
                    │     → Deterministic weighted combine  │
                    └──────────────────┬──────────────────┘
                                       │
                    ┌──────────────────▼──────────────────┐
                    │  5. Rank + Explain                   │
                    │     Top-K shortlist with evidence     │
                    │     Near-miss analysis + skill gaps   │
                    └──────────────────┬──────────────────┘
                                       │
                              ┌────────▼────────┐
                              │   JSON Output    │
                              └─────────────────┘
```

## Quick Start

### Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| **Python** | 3.12+ | Runtime |
| **[uv](https://docs.astral.sh/uv/)** | latest | Package manager |
| **Docker** | optional | Containerized dev/prod |

### 1. Clone and install

```bash
git clone https://github.com/shreyabaid007/staffer.git
cd staffer
uv sync --dev
```

### 2. Configure environment

```bash
cp .env.example .env
```

Fill in your API keys:

| Variable | Required | Purpose |
|----------|----------|---------|
| `DSM_CANDIDATE_ID_KEY` | Yes | HMAC key for candidate identity (must stay constant across runs) |
| `OPENROUTER_API_KEY` | Yes | Reasoning LLM (Claude Sonnet 4.6 via OpenRouter) |
| `MODAL_TOKEN_ID` | Yes | Modal serverless GPU for embeddings |
| `MODAL_TOKEN_SECRET` | Yes | Modal serverless GPU for embeddings |

### 3. Verify everything works

```bash
make check
```

This runs the full harness: format, lint, typecheck, unit tests, Tier 1 eval, and import contracts.

### Docker alternative

```bash
cp .env.example .env   # fill in API keys

# Production
docker compose up app

# Dev (runs make check inside the container)
docker compose run dev make check
```

## Usage

### CLI Commands

```bash
# Ingest candidate data (CSVs + resumes → bronze → silver → gold)
uv run dsm ingest

# Build the search index (gold candidates → embed → Milvus)
uv run dsm index

# Match a role → ranked shortlist (JSON to stdout)
uv run dsm match --role-id <ID>

# Match with full per-candidate explanations + lineage
uv run dsm explain --role-id <ID>
```

### Ingestion pipeline

```
Raw CSVs + PDFs
  → Bronze (immutable, content-addressed)
    → Silver (normalized, identity-resolved)
      → Gold (canonical, LLM-enriched via PII boundary)
        → Index (PII-free embeddings in Milvus Lite)
```

### Query pipeline

```
Role CSV → Parse demand
  → Gate (location + availability, deterministic)
    → Retrieve (hard-skill filter → hybrid recall → rerank)
      → Score (LLM sub-scores → weighted combine)
        → Rank (top-K + near-miss analysis)
          → JSON shortlist with evidence
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Language** | Python 3.12 |
| **Package manager** | [uv](https://docs.astral.sh/uv/) (reproducible lockfile) |
| **Data contracts** | Pydantic v2 (every module boundary is typed) |
| **LLM orchestration** | [DSPy](https://dspy-docs.vercel.app/) 2.0 (typed signatures, no raw prompts) |
| **Reasoning LLM** | Claude Sonnet 4.6 via [OpenRouter](https://openrouter.ai/) |
| **Embeddings** | `BAAI/bge-base-en-v1.5` (768-dim) on [Modal](https://modal.com/) serverless GPU |
| **Reranking** | `BAAI/bge-reranker-base` (cross-encoder) on Modal |
| **Vector store** | [Milvus Lite](https://milvus.io/docs/milvus_lite.md) (embedded, dense IP + BM25 sparse + RRF) |
| **PII protection** | [Presidio](https://microsoft.github.io/presidio/) + spaCy NER, deterministic redact-first |
| **Document parsing** | [Docling](https://github.com/DS4SD/docling) (PDF/resume extraction) |
| **CLI** | [Typer](https://typer.tiangolo.com/) |
| **Logging** | [structlog](https://www.structlog.org/) |
| **Type checking** | [Pyright](https://github.com/microsoft/pyright) (strict) |
| **Linting** | [Ruff](https://docs.astral.sh/ruff/) (format + lint) |
| **Import contracts** | [import-linter](https://import-linter.readthedocs.io/) (5 architectural contracts) |
| **Eval framework** | Three-tier `pytest` harness (AD-095/096) + [DeepEval](https://github.com/confident-ai/deepeval) G-Eval faithfulness judge (AD-105). _Promptfoo dropped in AD-095._ |

## Project Structure

```
staffer/
├── dsm/                          # Core package
│   ├── cli/                      #   Typer CLI (match, explain, ingest, index)
│   ├── ingest/                   #   Bronze → Silver → Gold pipeline
│   │   ├── land.py               #     CSV/PDF landing
│   │   ├── parse/                #     Format-specific parsers
│   │   ├── silver.py             #     Normalization + identity resolution
│   │   ├── enrich.py             #     LLM extraction via PII boundary
│   │   └── merge.py              #     Canonical gold assembly
│   ├── match/                    #   Query-time reasoning
│   │   ├── gates.py              #     Location + availability (pure Python)
│   │   ├── clarify.py            #     DSPy clarify (bounded LLM)
│   │   ├── score.py              #     LLM sub-scores + weighted combine
│   │   └── rank.py               #     Deterministic sort + top-K
│   ├── index/                    #   Retrieval infrastructure
│   │   ├── build.py              #     Gold → PII-free record → embed → upsert
│   │   ├── retrieve.py           #     Hybrid recall + rerank
│   │   └── milvus_store.py       #     Milvus Lite client (dense + sparse + RRF)
│   ├── pii/                      #   PII boundary (the only path to external LLMs)
│   │   ├── vault.py              #     Identity store (candidate_id ↔ name/email)
│   │   ├── redact.py             #     Deterministic redact + Presidio NER
│   │   ├── leakscan.py           #     Outbound leak-scan gate
│   │   └── pseudonymised_lm.py   #     Wraps all LLM calls with redact → scan → forward
│   ├── eval/                     #   Quality assurance
│   │   ├── invariants.py         #     6 deterministic evaluators (Tier 1)
│   │   ├── golden_set.py         #     31 hand-labelled cases / 95 labels
│   │   ├── faithfulness.py       #     G-Eval judge (threshold 0.70, TPR/TNR 1.00)
│   │   └── retrieval_quality.py  #     Recall@K + contextual precision
│   ├── models.py                 #   Frozen Pydantic v2 domain contracts
│   └── config.py                 #   Config loader (weights, K, models, adjacency)
├── modal/                        # Modal serverless embedder deployment
├── config/
│   ├── default.yaml              #   Weights, top-K, adjacency map, model IDs
│   ├── taxonomy.yaml             #   Skill taxonomy
│   └── prompts/                  #   DSPy prompt templates
├── specs/                        # Feature specs (requirements → design → tasks)
├── data/                         # Runtime data (gitignored)
│   ├── raw/                      #   Input CSVs + PDFs
│   ├── bronze/ silver/ gold/     #   Ingestion layers
│   └── index/                    #   Milvus Lite store
├── tests/                        # 444 tests (unit + eval, 3 tiers)
├── docs/                         # Living documentation
├── Makefile                      # Build harness
├── Dockerfile                    # Multi-stage (dev + prod)
├── docker-compose.yml            # Local dev / prod
└── pyproject.toml                # Dependencies + tool config
```

## Testing

### Test harness

| Command | Scope | LLM Required |
|---------|-------|:------------:|
| `make check` | Format + lint + typecheck + unit tests + Tier 1 eval + import contracts | No |
| `make check-all` | Everything above + full eval suite (Tier 1/2/3) | Tier 3 only |
| `make test` | Unit tests only | No |
| `make eval-tier1` | 6 deterministic invariant evaluators | No |
| `make eval` | Full eval (Tier 1 + 2 + 3) | Tier 3 only |
| `make smoke` | Real-data smoke test (opt-in, needs `data/raw/`) | Yes |

### Three-tier evaluation

| Tier | What it checks | How it runs |
|------|---------------|-------------|
| **Tier 1** | Gates respected, hard skills not cleared by adjacency, evidence cited, no PII leak, determinism, adjacency flagged | Code-based evaluators, no LLM |
| **Tier 2** | Signature regression, cassette freshness | Deterministic LM replay (offline) |
| **Tier 3** | Live smoke, cassette drift guard, faithfulness judge | Real LLM calls (needs API keys) |

### Architectural contracts (import-linter)

Five contracts enforced at build time ensure module boundaries are never violated:

1. **Gates** cannot import PII, index, or LLM code
2. **No direct LLM access** — all calls route through `PseudonymisedLM`
3. **Ingest** cannot import match or index
4. **Match/index** cannot import ingest (read-only serving side)
5. **Match** cannot import PII (wiring lives at the CLI composition root)

## Make Targets

| Command | Description |
|---------|-------------|
| `make check` | Full pre-commit harness (format + lint + typecheck + test + eval-tier1 + imports) |
| `make check-all` | `make check` + full eval suite |
| `make format` | `ruff format` |
| `make lint` | `ruff check --fix` |
| `make typecheck` | `pyright` |
| `make test` | `pytest tests/ -v` (excludes eval) |
| `make eval` | `pytest tests/eval` (all tiers) |
| `make eval-tier1` | `pytest tests/eval/test_invariants.py` (code-based only) |
| `make eval-record` | Record LLM cassettes for offline replay |
| `make smoke` | Opt-in real-data smoke test |
| `make docker` | Build production Docker image |
| `make docker-dev` | Build dev Docker image |

## Documentation

| Document | What it covers |
|----------|---------------|
| [Product spec](docs/product.md) | What we're building, who it's for, invariants, scope |
| [Tech spec](docs/tech.md) | Stack, architecture, rules, module contracts |
| [Structure](docs/structure.md) | Repo layout, spec format, module boundaries |
| [Decisions](docs/decision.md) | 106 Architecture Decision Records (ADRs) |
| [Progress](docs/progress.md) | Current build state across all lanes |

## Architecture Decisions

All significant decisions are recorded as numbered ADRs in [docs/decision.md](docs/decision.md). Notable ones:

| ADR | Decision |
|-----|----------|
| AD-060 | Frozen Pydantic v2 contracts at every module boundary |
| AD-066 | Bronze → Silver → Gold immutable data layers |
| AD-069 | Redact-first + outbound leak-scan PII gate |
| AD-074 | Modal serverless GPU for embeddings + reranking |
| AD-082 | Content+version hash gating for re-embedding |
| AD-090 | Deterministic weighted score combine (0.7 skill / 0.3 feedback) |
| AD-095 | Three-tier eval harness (code → cassette → live) |
| AD-101 | `PseudonymisedLM` as the only path to external LLMs |
| AD-104 | Golden set: 31 cases / 95 labels for eval calibration |

## Contributing

This project follows a **spec-driven workflow**:

1. **Read** `docs/progress.md` and relevant docs before starting
2. **Spec first** — write `specs/<feature>/requirements.md` + `design.md` + `tasks.md` before any code
3. **One task = one commit** — imperative message referencing the spec
4. **`make check` must be green** before every commit
5. **Record decisions** in `docs/decision.md` for anything non-trivial

See [CLAUDE.md](CLAUDE.md) for the full operating rules.

## License

Private / internal use only.
