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
    %% ── FOUNDATION: what exists before any feature ──
    subgraph FOUNDATION["FOUNDATION — read these first, every session"]
        direction LR
        P["product.md\nWhat to build + scope"]
        T["tech.md\nStack + dependencies"]
        S["structure.md\nLayout + spec format"]
        D["decision.md\nSettled calls ADRs"]
        PR["progress.md\nShared index of main"]
        LN["progress.LANE.md\nYour lane state"]
    end

    %% ── STEP 1: ORIENT ──
    START(["Engineer starts a session"]) --> ORIENT
    ORIENT["STEP 1 — ORIENT\nRead progress.md then your lane file\nRead decision.md for settled calls\nUnderstand what is done and what is next"]
    FOUNDATION -.->|"these inform\nevery step"| ORIENT

    %% ── STEP 2: SPEC ──
    ORIENT --> SPEC
    SPEC["STEP 2 — SPEC\nCreate specs/lane-seq-slug/ with 3 files:\n  requirements.md — EARS acceptance criteria\n  design.md — contracts, edge cases, eval cases\n  tasks.md — ordered atomic tasks, each = 1 commit"]

    SPEC --> GATE{"HUMAN SIGN-OFF\non the spec?"}
    GATE -->|"Revise"| SPEC
    GATE -->|"Approved"| TASK

    %% ── STEP 3: IMPLEMENT one task ──
    TASK["STEP 3 — IMPLEMENT one task\nWrite code in dsm/ with Pydantic types\nWrite test in tests/\nOne task = one commit"]

    %% ── STEP 4: VERIFY ──
    TASK --> HARNESS

    subgraph HARNESS["STEP 4 — make check (must be GREEN before commit)"]
        direction LR
        H1["ruff\nformat + lint"]
        H2["pyright\ntypecheck"]
        H3["pytest\nunit tests"]
        H4["import-linter\nno illegal deps"]
    end

    HARNESS --> RESULT{"GREEN?"}
    RESULT -->|"RED — fix the cause\nnever disable a check"| TASK
    RESULT -->|"GREEN"| COMMIT["git commit\nOne task = one commit\nMsg references the spec"]

    %% ── Loop back for more tasks ──
    COMMIT --> MORE{"More tasks\nin the spec?"}
    MORE -->|"Yes — next task"| TASK

    %% ── STEP 5: RECORD ──
    MORE -->|"No — all tasks done"| RECORD
    RECORD["STEP 5 — RECORD\nAppend any new ADRs to decision.md\nRun /handoff to update progress.LANE.md\nFix any docs that drifted in the same PR"]

    %% ── STEP 6: MERGE ──
    RECORD --> PROPEN["STEP 6 — PR + MERGE\nOpen pull request\nAll criteria met + harness GREEN"]
    PROPEN --> MERGED["Merged to main"]
    MERGED --> HINDEX["Run /handoff-index\nRefreshes progress.md to match main"]

    HINDEX --> NEXT(["Next feature — back to ORIENT"])
    NEXT --> ORIENT

    %% ── PARALLEL LANES sidebar ──
    subgraph LANES["3 ENGINEERS WORK IN PARALLEL"]
        direction TB
        LA["Lane A — Data: ingest/ index/ modal/\nBranch: feat/a/NNN-slug"]
        LB["Lane B — Reasoning: match/clarify match/score\nBranch: feat/b/NNN-slug"]
        LC["Lane C — Quality: gates rank pii cli eval\nBranch: feat/c/NNN-slug"]
        GLUE["Glued by frozen dsm/models.py\nEach lane touches only its own files\nEach lane updates only its own progress file"]
    end

    LANES -.->|"each lane follows\nthis same loop"| ORIENT

```

### Data Pipeline


```mermaid

flowchart TD
    %% ── INPUTS ──
    subgraph INPUTS["DATA INPUTS"]
        direction TB
        XLSX["Supply Sheets (xlsx)\nBeach / Rolling Off / New Joiners\nJoined by email"]
        PDF["Consultant Profiles (PDF)\nParsed by Docling"]
        FB["Feedback Records\nInternal EE + Client\nEqual weight in score"]
        ROLE["Open Role Description\nOne role at a time"]
    end

    %% ── PHASE 1: INGEST ──
    XLSX --> INGEST
    PDF --> INGEST
    FB --> INGEST
    INGEST["INGEST (dsm/ingest/)\nParse xlsx + Docling PDFs + feedback\nJoin on email\nOutput: dict of Candidate models + OpenRole"]

    %% ── PHASE 2: PII STRIP + EMBED ──
    INGEST --> PII_STRIP
    PII_STRIP["PII BOUNDARY (dsm/pii/)\nPresidio + spaCy local NER\nStrip name/email from embedding text\nPseudonymise before any external call"]

    PII_STRIP --> EMBED
    EMBED["EMBED (Modal GPU)\nBAAI/bge-base-en-v1.5\nReceives PII-free text only\nBatch embed all candidates"]

    EMBED --> INDEX
    INDEX["INDEX (dsm/index/)\nMilvus Lite embedded\nHybrid: dense + BM25 + RRF"]

    %% ── PHASE 3: CLARIFY THE ROLE ──
    ROLE --> CLARIFY
    CLARIFY["CLARIFY (dsm/match/clarify.py)\nLLM via PseudonymisedLM\nDSPy typed Signature\nRaw role text --> TargetProfileScorecard\nExtracts: skills, location, dates, hard vs desired"]

    %% ── PHASE 4: DETERMINISTIC GATES ──
    INGEST --> GATES
    CLARIFY --> GATES
    GATES["GATES (dsm/match/gates.py)\nPure Python — NO LLM, no imports from pii/ or index/\n─────────\nLocation gate: co-location check or remote-India\nAvailability gate: free by role start + 14 days\n─────────\nOutput: EligiblePool + ExclusionLog with reasons"]

    %% ── PHASE 5: RETRIEVE ──
    GATES --> RETRIEVE
    INDEX --> RETRIEVE
    CLARIFY --> RETRIEVE
    RETRIEVE["RETRIEVE (dsm/index/)\nHybrid search over EligiblePool only\nScorecard query --> top-K candidates\nDeterministic retrieval, not agentic"]

    %% ── PHASE 6: SCORE ──
    RETRIEVE --> SCORE
    CLARIFY --> SCORE
    SCORE["SCORE (dsm/match/score.py)\nLLM via PseudonymisedLM\nDSPy typed Signature\nPer candidate: skill match + feedback assessment\nPython computes: 0.7 x skill + 0.3 x feedback\nAdjacency: partial credit + flag, never clears hard skill\nNew joiner skills flagged unverified"]

    %% ── PHASE 7: RANK ──
    SCORE --> RANK
    RANK["RANK (dsm/match/rank.py)\nDeterministic Python sort + tie-break + top-k\nConfig-free — orchestrator owns config\nOutput: ShortlistResult OR NoMatchResult\n─────────\nShortlist: ranked candidates + explanations + flags\nNo-match: reason + ordered near-misses"]

    %% ── OUTPUT ──
    RANK --> OUTPUT

    subgraph OUTPUT["CLI OUTPUT (dsm/cli/)"]
        direction TB
        JSON_OUT["ShortlistResult JSON\nRanked top-5 candidates\nPer-candidate: structured fields + narrative\nEvery claim cites real evidence"]
        TRADEOFFS["Trade-offs surfaced, never hidden\nRetention risk / unverified skills /\nuncertain roll-off / adjacency used"]
        NOMATCH["OR NoMatchResult\nEmpty + reason + closest near-misses\nNever a forced match"]
    end

    %% ── PII BOUNDARY ANNOTATION ──
    PII_STRIP -.->|"pseudonymises"| CLARIFY
    PII_STRIP -.->|"pseudonymises"| SCORE

    %% ── STYLING ──
    style INPUTS fill:#e3f2fd,stroke:#1565C0
    style INGEST fill:#e8f5e9,stroke:#2E7D32
    style PII_STRIP fill:#fce4ec,stroke:#c62828
    style EMBED fill:#fff3e0,stroke:#E65100
    style INDEX fill:#fff3e0,stroke:#E65100
    style CLARIFY fill:#f3e5f5,stroke:#7B1FA2
    style GATES fill:#e8f5e9,stroke:#2E7D32
    style RETRIEVE fill:#fff3e0,stroke:#E65100
    style SCORE fill:#f3e5f5,stroke:#7B1FA2
    style RANK fill:#e8f5e9,stroke:#2E7D32
    style OUTPUT fill:#e3f2fd,stroke:#1565C0

```
