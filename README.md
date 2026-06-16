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


## Quick Flow Diagrams

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
