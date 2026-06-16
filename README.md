# Staffer — Demand–Supply Matcher

A staffing decision engine: given one open role, returns a ranked, explainable shortlist of consultants with trade-offs surfaced for a human to decide.

## Quick start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)
- Docker (optional)

### Local setup

```bash
git clone https://github.com/shreyabaid007/staffer.git
cd staffer

# Install dependencies
uv sync --dev

# Copy and fill in API keys
cp .env.example .env

# Run the harness
make check
```

### Docker setup

```bash
cp .env.example .env   # fill in API keys

# Production
docker compose up app

# Dev (run tests, lint, typecheck)
docker compose run dev make check
```

### Makefile targets

| Command         | What it does                          |
| --------------- | ------------------------------------- |
| `make check`    | format + lint + typecheck + test + eval |
| `make format`   | ruff format                           |
| `make lint`     | ruff check --fix                      |
| `make typecheck`| pyright                               |
| `make test`     | pytest tests/ -v                      |
| `make docker`   | build prod image                      |
| `make docker-dev`| build dev image                      |

## Docs

- [Product](docs/product.md) — what we're building and why
- [Tech](docs/tech.md) — stack, rules, architecture
- [Structure](docs/structure.md) — repo layout and module contracts
- [Decisions](docs/decision.md) — ADRs
- [Progress](docs/progress.md) — current state of the build


## Quick Diagrams

### How We Build Features (spec-driven, harness-verified, lane-parallel)

```mermaid

flowchart TD
    START(["Engineer starts a session"]) --> ORIENT

    %% ===== FOUNDATION (feedforward) =====
    subgraph FOUNDATION["📚 FOUNDATION — read first, every session"]
        direction LR
        F1["product.md · tech.md · structure.md"]:::ff
        F2["decision.md<br/>settled ADRs"]:::ff
        F3["progress.md + your lane file<br/>where the build is"]:::ff
    end

    ORIENT["1 · ORIENT<br/>read index → lane file → ADRs"]:::step
    FOUNDATION -. "inform every step" .-> ORIENT

    ORIENT --> SPEC["2 · SPEC<br/>specs/&lt;lane&gt;-&lt;seq&gt;-&lt;slug&gt;/<br/>requirements · design · tasks"]:::step

    SPEC --> GATE{"✋ HUMAN<br/>SIGN-OFF?"}:::gate
    GATE -- revise --> SPEC

    %% ===== INNER LOOP: one task at a time =====
    GATE -- approved --> TASK
    subgraph INNER["🔁 INNER LOOP — one task = one commit"]
        direction TB
        TASK["3 · IMPLEMENT one task<br/>code + Pydantic types + test"]:::step
        TASK --> HARNESS["4 · VERIFY — make check"]:::harness
        HARNESS --> RESULT{"GREEN?"}:::gate
        RESULT -- "RED · fix the cause,<br/>never disable a check" --> TASK
        RESULT -- green --> COMMIT["git commit<br/>msg references the spec"]:::step
        COMMIT --> MORE{"more tasks?"}:::gate
        MORE -- "yes" --> TASK
    end

    HCHECKS["make check =<br/>ruff · pyright · pytest · import-linter"]:::note
    HCHECKS -.- HARNESS

    %% ===== OUTER LOOP: feature done → merge =====
    MORE -- "no · all tasks done" --> RECORD["5 · RECORD<br/>new ADRs → decision.md<br/>/handoff → your lane file<br/>fix drifted docs in same PR"]:::step
    RECORD --> PR["6 · PR + MERGE<br/>all criteria met · harness green"]:::step
    PR --> MERGED["✅ merged to main"]:::done
    MERGED --> HINDEX["/handoff-index<br/>refresh progress.md to match main"]:::step
    HINDEX --> NEXT(["next feature"]) --> ORIENT

    %% ===== PARALLEL LANES =====
    subgraph LANES["👥 THREE LANES IN PARALLEL"]
        direction TB
        LA["A · Data — ingest / index / modal"]:::lane
        LB["B · Reasoning — clarify / score"]:::lane
        LC["C · Quality — gates / rank / pii / cli / eval"]:::lane
        GLUE["glued by frozen dsm/models.py<br/>each lane edits only its own files + lane file"]:::note
    end
    LANES -. "each lane runs this same loop" .-> ORIENT

    classDef ff fill:#e8f0fe,stroke:#4285f4,stroke-width:1.5px;
    classDef step fill:#ffffff,stroke:#5f6368,stroke-width:1.5px;
    classDef gate fill:#fef7e0,stroke:#f9ab00,stroke-width:2px;
    classDef harness fill:#e6f4ea,stroke:#34a853,stroke-width:2px;
    classDef done fill:#e6f4ea,stroke:#188038,stroke-width:2px;
    classDef lane fill:#f3e8fd,stroke:#a142f4,stroke-width:1.5px;
    classDef note fill:#fafafa,stroke:#bdbdbd,stroke-width:1px,color:#5f6368;
    style FOUNDATION fill:#fbfdff,stroke:#90a4ae
    style INNER fill:#f6fbf7,stroke:#66bb6a
    style LANES fill:#fdf7ff,stroke:#ba68c8

```

### Data Pipeline


```mermaid

flowchart TB
    %% ===== LEGEND =====
    subgraph LEGEND["LEGEND — who does the work"]
        direction LR
        L1["Deterministic<br/>Python"]:::pure
        L2["LLM step<br/>(DSPy typed)"]:::llm
        L3["External<br/>infra"]:::infra
        L4["PII<br/>boundary"]:::pii
        L5["I/O"]:::io
    end

    %% ===== CANDIDATE INDEXING PATH =====
    subgraph CAND["CANDIDATE PATH — build the searchable index (offline, batch)"]
        direction TB
        XLSX["Supply sheets + profiles + feedback<br/>(xlsx · PDF · records)"]:::io
        INGEST["INGEST · dsm/ingest/<br/>parse + join on email<br/>→ Candidate models"]:::pure
        STRIP["PII STRIP · dsm/pii/<br/>drop name/email from embed text<br/>(by construction)"]:::pii
        EMBED["EMBED · Modal GPU<br/>bge-base-en-v1.5<br/>PII-free text only"]:::infra
        INDEX["INDEX · dsm/index/<br/>Milvus Lite · dense+BM25+RRF"]:::infra
        XLSX --> INGEST --> STRIP --> EMBED --> INDEX
    end

    %% ===== ROLE QUERY PATH =====
    subgraph QUERY["ROLE PATH — answer one open role"]
        direction TB
        ROLE["Open role description"]:::io
        CLARIFY["CLARIFY · clarify.py<br/>role text → TargetProfileScorecard<br/>(skills · location · dates · hard vs desired)"]:::llm
        GATES["GATES · gates.py — PURE, NO LLM<br/>location + availability (start +14d)<br/>→ EligiblePool + ExclusionLog"]:::pure
        RETRIEVE["RETRIEVE · dsm/index/<br/>hybrid search over eligible only<br/>→ top-K"]:::infra
        SCORE["SCORE · score.py<br/>LLM sub-scores → Python: 0.7·skill + 0.3·feedback<br/>adjacency never clears a hard skill · new joiner = unverified"]:::llm
        RANK["RANK · rank.py<br/>deterministic sort + tie-break + top-k"]:::pure
        ROLE --> CLARIFY --> GATES --> RETRIEVE --> SCORE --> RANK
    end

    %% ===== CONVERGENCE =====
    INGEST -. "candidates" .-> GATES
    INDEX -. "vector index" .-> RETRIEVE

    %% ===== PII WALL =====
    PIIW{{"PseudonymisedLM — the ONLY path to OpenRouter<br/>pseudonymise before · de-pseudonymise after"}}:::pii
    CLARIFY -.->|through| PIIW
    SCORE -.->|through| PIIW

    %% ===== OUTPUT =====
    RANK --> ORCH["ORCHESTRATOR · dsm/cli/<br/>builds final result + owns config"]:::pure
    ORCH --> OUT["ShortlistResult — ranked top-5<br/>structured fields + narrative · every claim cited<br/>trade-offs surfaced (retention · unverified · roll-off)"]:::io
    ORCH --> NOM["…or NoMatchResult<br/>empty + reason + ordered near-misses<br/>never a forced match"]:::io

    classDef pure fill:#e6f4ea,stroke:#2E7D32,stroke-width:1.5px;
    classDef llm fill:#f3e5f5,stroke:#7B1FA2,stroke-width:1.5px;
    classDef infra fill:#fff3e0,stroke:#E65100,stroke-width:1.5px;
    classDef pii fill:#fce4ec,stroke:#c62828,stroke-width:1.5px;
    classDef io fill:#e3f2fd,stroke:#1565C0,stroke-width:1.5px;
    style LEGEND fill:#fafafa,stroke:#bdbdbd
    style CAND fill:#fbfdff,stroke:#90a4ae
    style QUERY fill:#fbfdff,stroke:#90a4ae

```
