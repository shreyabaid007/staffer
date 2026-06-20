# progress.md — Live build state (index)

> **Read this first at the start of every session, then your lane file `docs/progress.<lane>.md`.** This is the shared index: global facts only (build phase, what works, active specs, decisions). Per-lane state lives in the lane files. (The rules of *how* we work live in `CLAUDE.md`.) A fresh session should be able to orient from this file alone.
> Per-lane progress goes in `docs/progress.A.md` / `.B.md` / `.C.md` — see _Lane files_ below. Section headers are stable so `/handoff-index` can target them. Keep them.

## Current status
- **Build phase:** **Real ingestion reaches gold + Modal embedder/reranker deployed.** Landing + parse → bronze → silver → LLM-enriched gold is live. **BGE embedder (`bge-base-en-v1.5`) + cross-encoder reranker (`bge-reranker-base`) deployed to Modal serverless GPU (T4)** with typed `EmbedClient` protocol in `dsm/index/` (AD-074/AD-071/AD-080). Index/retrieval pipeline (Milvus, `embed_text` from gold, hybrid recall) not yet wired. `PseudonymisedLM` anonymise/deanonymise still the Slice-0 pass-through stub. Contracts frozen with signed-off amendments — AD-075/076/077.
- **Active slice:** Most recent merge to `main` is Modal embedder + reranker deployment (PR #14, `feat/a/004-modal-embed-rerank`). Prior: ingestion enrich→gold (PR #12) + tombstone fix (PR #13).
- **Harness (`make check`):** GREEN — format, lint, typecheck, 249 tests (11 skipped — opt-in real-data + Modal smoke), 3 import contracts all pass.
- **`main`:** Slice 0 foundation (PR #3) + per-lane progress files (PR #4); index refresh (PR #6); real gates/rank/no-match (PR #5, `feat/c/001-gates-rank`); ingestion architecture docs + AD-065…AD-074 (PR #9); ingestion landing+parse → bronze (PR #10, `feat/a/001-ingest-landing-parse`); ingestion silver + identity vault seed (PR #11, `feat/a/002-ingest-silver-identity`); ingestion enrich → canonical gold (PR #12, `spec/a/003-ingest-enrich-gold`) + re-run tombstone fix (PR #13); **Modal embedder + reranker deployment (PR #14, `feat/a/004-modal-embed-rerank`)**.

## Works end-to-end right now
- `uv run dsm match --role-id ROLE-STUB-01` — runs the full pipeline (real gates → stub retrieve/score → real rank, or real no-match) over stub ingest and prints a valid `ShortlistResult` / `NoMatchResult` JSON.
- `uv run dsm ingest` — lands raw files (`data/raw` → bronze), parses (CSV/PDF/markdown) → bronze, normalizes → silver `NormalizedRecord`s, then **enriches resumes/feedback through the PII boundary (redact → leak-scan → DSPy extract @ temp 0 → de-anonymize → verify-quote) and merges silver+enriched into one canonical `GoldCandidate` per `candidate_id`** (§7 authority, resume↔feedback conflicts recorded never averaged), reconciles against prior gold (tombstones departed; an idempotent re-run is a no-op for gold), writes `gold/<cid>.json` (+`gold_hash`), and prints PII-safe `── Silver ──` + `── Gold ──` summaries; exits 1 on errors / leak-block. `make smoke` runs an opt-in real-data smoke.
- **Modal embedder + reranker** ([`modal/embedder.py`](../modal/embedder.py)) — `BAAI/bge-base-en-v1.5` (768-dim, L2-normalized, asymmetric passage/query) + `BAAI/bge-reranker-base` (cross-encoder) deployed to Modal serverless T4 GPU as `staffer-models` app. Weights baked into the container image (zero-download cold start). `DSM_MODAL_SMOKE=1 uv run pytest tests/index/test_modal_smoke.py` verifies the live deployment. Typed [`EmbedClient`](../dsm/index/embed_client.py) protocol + `ModalEmbedClient` implementation in `dsm/index/embed_client.py` (AD-074/AD-071/AD-080).
- **Reusable PII layer** ([`dsm/pii/`](../dsm/pii/)) — generic `redact.py` (deterministic known-PII strip first + Presidio NER seam + de-anonymize), `leakscan.py` (outbound hard gate, fails the build on residual PII), and the `vault.py` `candidate_id = HMAC(email)` derivation; ingest reaches the LLM only through `PseudonymisedLM` (AD-078). Encrypted at-rest vault + NER/org-dictionary hardening are Lane C, later.
- **Real deterministic gates** ([`dsm/match/gates.py`](../dsm/match/gates.py)) — location (AD-020/063a) + availability (AD-021/022); LLM-free, import-clean.
- **Real ranking** ([`dsm/match/rank.py`](../dsm/match/rank.py)) — deterministic sort/tie-break/top-k; config-free (orchestrator owns config via [`dsm/config.py`](../dsm/config.py), AD-064).
- **No-match path + near-misses** ([`dsm/cli/commands.py`](../dsm/cli/commands.py)) — orchestrator builds `NoMatchResult` with ordered, capped near-misses (AD-063b/c/d).
- `dsm/models.py` — frozen Pydantic v2 domain contracts (AD-060), with `Location.city` optional (AD-075) and `EvidenceCitation` carrying optional `source_hash`/`locator` as the verified-quote type (AD-077).
- `make check` — 249 tests green (11 skipped — opt-in real-data + Modal smoke), 0 type errors, 3 import contracts (gates ⊥ PII/index/LLM; no direct LLM provider access; ingest ⊥ match/index — ingest may now use the `dsm.pii` boundary + `dspy`, the sanctioned LLM path, AD-078). Importable ROLE-01/02/03 seed fixtures in `tests/fixtures/` (reusable by `dsm/eval/`).

## Lane files
Per-lane In flight / Next up / Blockers / Session log live in these append-only files. Read the index, then your lane file.
- [`docs/progress.A.md`](progress.A.md) — **Lane A: Data & Retrieval** (Eng A — ingest, index, gates, retrieval).
- [`docs/progress.B.md`](progress.B.md) — **Lane B: Reasoning** (Eng B — clarify, score, rank).
- [`docs/progress.C.md`](progress.C.md) — **Lane C: Quality, PII & Interface** (Eng C — PII boundary, CLI, eval/quality).

## Active specs
- `specs/000-foundation/` — complete, approved, merged to `main`.
- `specs/c-001-gates-rank/` — complete, approved, merged to `main` (PR #5).
- `specs/a-001-ingest-landing-parse/` — complete, approved, merged to `main` (PR #10). First ingestion slice: landing + parse → bronze.
- `specs/a-002-ingest-silver-identity/` — complete, approved, merged to `main` (PR #11). Second ingestion slice: bronze → silver normalization + identity vault seed (AD-075/076).
- `specs/a-003-ingest-enrich-gold/` — complete, approved, merged to `main` (PR #12, + fix PR #13). Third ingestion slice: LLM enrich (PII-bracketed, cited) + canonical merge → gold + reconcile/tombstones (AD-077/078/079).
- `specs/a-004-modal-embed-rerank/` — complete, approved, merged to `main` (PR #14). Modal deployment of BGE embedder + reranker + typed `EmbedClient` protocol (AD-080).
- _Signed-off design (not a spec):_ [`ee-ingestion-architecture.md`](../ee-ingestion-architecture.md) — full ingestion subsystem, accepted as AD-065…AD-074. a-001/a-002/a-003 implement its landing+parse, silver, and enrich+gold stages; a-004 deploys the embedding/reranking models to Modal.
- _Next (planned):_ Lane A follow-on spec for the index/retrieval wiring (build `embed_text` from gold, upsert to Milvus Lite, replace stub retriever with hybrid dense+BM25+RRF recall + rerank, AD-072/074); Lane C `c-002-*` for the real `PseudonymisedLM` provider/anonymiser, NER + client-org-dictionary hardening, and the encrypted identity-vault write path. Neither written yet.

## Decisions
- Authoritative log: `docs/decision.md` (current range AD-001 … AD-080; next starts at AD-081). Recently landed (a-004): **AD-080 — add `models.reranker` to config** (`BAAI/bge-reranker-base` in `config/default.yaml`, tech.md rule 6; Modal container duplicates the ID with a comment pointing to config as source of truth). Prior (a-003): **AD-077/078/079** (EvidenceCitation relax, ingest→pii+dspy import relax, feedback facts in gold / score deferred). Prior: **AD-075/076** (silver/identity) and **AD-065…AD-074** (ingestion architecture).
- **Freeze the contracts after Slice 0.** Churn breaks parallel lane work — change only via team agreement + a new ADR.

---

## Session log — pre-split archive (frozen)
Shared history from before the per-lane split (2026-06-14). **Frozen — do not append here.** New entries go in the lane files' session logs.
- **2026-06-11 · slice-0-foundation** — Completed tasks F-005 through F-016: stub ingest/gates/clarify/score/rank/index/pii modules, CLI `dsm match`, `config/default.yaml`, eval scaffold, import-linter passing, 29 tests green. `make check` fully green. Slice 0 done. Next: merge to main, begin Slice 1 (real gates).
- **2026-06-11 · slice-0-models** — Implemented tasks F-003 + F-004: wrote `dsm/models.py` (19 Pydantic v2 frozen models, all enums as `StrEnum`) and `tests/test_models.py` (27 tests — instantiation, discriminated union, validation rejection). `make check` green. Added AD-060 (contracts frozen). Also fixed `pyproject.toml` import-linter config (`include_external_packages = true`). Next: F-005 stub ingest through F-016.
- **2026-06-11 · slice-0-spec** — Wrote and approved `specs/000-foundation/` (requirements, design, tasks). 16 tasks (F-001 to F-016) defined for frozen contracts + stubbed CLI + green harness. Committed to branch `spec/000-foundation`. No implementation yet. Next: execute tasks.
- **2026-06-08 · setup** — Created the operating-system layer (`CLAUDE`, `product`, `tech`, `structure`, `decision`) and this file. No code. Next: Slice 0 foundation.

---

## Maintaining this file
This index describes `main`. It is refreshed **only at merge to `main`**, by whoever merges, via `/handoff-index` — which rewrites the global sections (_Current status_, _Works end-to-end_, _Active specs_, _Decisions_). See `.claude/commands/handoff-index.md`. While working on a feature branch, do **not** edit this file — update only your own lane file via `/handoff` (lane resolved from `.claude/lane`; see `.claude/commands/handoff.md`).
