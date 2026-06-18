# progress.A.md — Lane A: Data & Retrieval

> Lane file. Owner: **Eng A — Data & Retrieval** (ingest, index, gates, retrieval).
> Global facts (build phase, contracts, decisions) live in the index `docs/progress.md` — don't duplicate them here.
> Headers below are stable so `/handoff` can target them. Append to the session log, newest first; rewrite the other sections from current state.

## In flight
- **`a-001-ingest-landing-parse`** — complete on branch `feat/a/001-ingest-landing-parse`, harness GREEN. Awaiting PR + merge to `main` (then `/handoff-index`).

## Next up
1. Merge `a-001` to `main` (open PR; refresh the index via `/handoff-index`).
2. **Silver stage** (ee-ingestion §6 phase 3) — `BronzeRecord → NormalizedRecord`: type coercion, normalization, identity resolution (`candidate_id = HMAC(email)`, AD-067), taxonomy skill mapping + unmapped queue. Still LLM-free.
3. **Enrich → merge → gold** — PII-bracketed LLM extraction (cited evidence per AD-073), provenance-weighted merge to one `Candidate` per `candidate_id`, snapshot reconcile + tombstones (AD-070).
4. Real index/retrieval over the vector store (Milvus Lite + Modal BGE embed, AD-072/AD-074). _(Gates are already real — c-001, on `main`.)_

## Blockers / needs a human
- _(none)_

## Session log (append-only — newest first)
- **2026-06-18 · a-001-ingest-landing-parse** — Implemented Slice 1 bronze foundation (T-001…T-010): `data/` layout + gitignored PII-dense layers; bronze models (`SourceType`/`LandingStatus`/`ManifestEntry`/`BronzeRecord`/`RunManifest`); content-addressed `LocalFSBlobStore` (atomic temp+rename); `JSONLManifest` (append-only commit marker); `land.py` (discover · classify · sha256 · dedup · blob-before-manifest crash recovery); CSV parser (banner/as-of, quoting, verbatim rows, log+skip); PDF parser (Docling → sections + `email_found` + OCR fallback, behind a mockable `_extract` seam for offline tests); markdown feedback parser (email-keyed items); `lineage.py` seed (structlog `log_invalid` + `build_run_manifest`); new import-linter contract isolating `dsm.ingest`; e2e land+parse test. Added `structlog` dep (already in tech.md §14). `make check` **GREEN** (112 tests, 3 contracts). Scope: CSV+PDF+MD bronze parsing all landed now — supersedes the old "sheets-only in Slice 1" note (scope sequencing, not an ADR). Deferred to later slices: silver/normalize/`candidate_id`/taxonomy/enrich/gold/PII redaction/embedding/reconcile. Next: merge, then the silver stage.
- **2026-06-14 · lane-split** — Split shared `progress.md` into per-lane files; seeded this Lane A file from the Data & Retrieval slices. `make check` GREEN (29 tests, 2 import contracts). Next: Slice 1 real gates.
