# Lane Ownership — Demand–Supply Matcher

> Authoritative lane assignments (AD-062). The handoff convention (lane files, `/handoff`, `/handoff-index`) remains per AD-061.

## Lanes

### Lane A — Data & Retrieval (Eng A)
**Owns:** `dsm/ingest/`, `dsm/index/`, `modal/`, `data/`

| Slice | Scope |
| --- | --- |
| A1 — Ingest | CSV supply + Docling resume + feedback parsers → bronze/silver/gold; vault refs; content-hashed cache |
| A2 — Index | Modal embed client; Milvus client; hybrid retrieval + rerank |

### Lane B — Reasoning (Eng B)
**Owns:** `dsm/match/clarify.py`, `dsm/match/score.py`

| Slice | Scope |
| --- | --- |
| B1 — Clarify | DSPy signature: raw role → TargetProfileScorecard |
| B2 — Score | DSPy signature: scorecard + candidate → CandidateAssessment |

### Lane C — Quality, PII & Interface (Eng C)
**Owns:** `dsm/match/gates.py`, `dsm/match/rank.py`, `dsm/pii/`, `dsm/cli/`, `dsm/eval/`, `Makefile`/CI

| Slice | Days | Scope |
| --- | --- | --- |
| C1 — Gates + rank | 1–3 | Real location + availability gates; deterministic ranking; orchestrator no-match path; test fixtures ROLE-01/02/03 |
| C2 — Real PseudonymisedLM | 4–6 | Presidio + spaCy behind the unchanged wrapper; no PII to OpenRouter/Modal |
| C3 — Evals live | 7–9 | Seed eval cases + five invariants; `make check-all` becomes the team gate |
| Day 10 | | Full suite on integrated system; fix list; demo prep |
