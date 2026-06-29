# Backlog — known debt & deferred work (iteration-2 candidates)

> One place for work that is **decided-but-deferred** or **known debt**, consolidated from the lane
> files' "Next up" sections and the ADRs that explicitly defer something. Live build state is in
> `docs/progress.md`; settled rationale is in `docs/decision.md`. This file is a to-do list, not a
> source of truth — when an item is picked up, write a spec (`specs/<feature>/`, golden rule 1) and
> move the detail there.

## PII boundary hardening (highest priority — real residual exposure)
- **c-005 · Generic outbound NER/org-dictionary scan on the index/embed path (AD-084).** Third-party
  / client-org names in gold `projects` can still reach Modal's embedder unredacted —
  `dsm/index/text_builder.build_embed_text` takes no `known_pii` and is the single attach point. The
  query-score boundary's index-path counterpart. **Until this lands, the index path is NOT fully
  PII-clean** (don't describe it as such). _Owner: Lane C. Spec-first._
- **Encrypted, retention-limited identity vault (AD-068).** `FileVault` is **plaintext** today
  (gitignored, signed-off POC limitation, AD-102) — and AD-107 now surfaces real identity in output,
  raising visible exposure. Needs: encryption at rest + retention limits + purge-by-`candidate_id`.
- **Vault-miss posture revisit (AD-103).** Currently warn-only: an empty vault + degraded NER
  (missing `en_core_web_lg`) means a candidate's de-anonymised gold free-text can reach the provider.
  Accepted for now; subsumed by c-005 + AD-068. A startup check that errors when `pii.ner_enabled` is
  true but the spaCy model is absent was **declined** for this round — revisit alongside c-005.

## Deprecated / dead code to resolve
- **c-001 deprecation close-out (AD-085).** `gates.py`/`rank.py` were rebuilt by b-001/b-002 and are
  live, but AD-085 left the c-001-era *design* "deprecated, code remains in tree". Audit for any
  genuinely-dead c-001 path and either delete it or formally un-deprecate; don't carry ambiguous
  zombie code into an iteration-2 refactor.
- **`clarify.py` "Slice-0 stub" docstring.** The deterministic fallback in `dsm/match/clarify.py` is
  documented as "(the Slice-0 stub)" — reword to "deterministic echo fallback" so it doesn't read as
  unimplemented.

## Retrieval / ranking
- **Tune the recall trip point (AD-109/AD-089).** Hybrid recall ships ON (`index.recall.enabled`).
  `top_n` (=100) bounds the dense/BM25/RRF fan-out — tune against real gated-pool sizes (§12 #6); set
  the flag off to force the exhaustive path on tiny pools.
- **Distributed-gate `detail` wording.** The country-mismatch `LOCATION_MISMATCH` `detail` in
  `filter_candidates` still reuses co-location phrasing (dormant — all data is India). Tidy when the
  distributed gate gets real exercise.

## Ingestion / pipeline wiring
- **Chain `dsm index` onto `dsm ingest`.** Standalone `dsm index` is delivered; auto-running the
  index phase at the end of `ingest` is a small follow-on (`ingest` does not invoke `index_gold`
  today).
- **Live end-to-end validation.** A real `dsm match` / `dsm ingest` run needs a built Milvus index +
  Modal (embed/rerank) + an OpenRouter key + gold on disk + a demand CSV. `make check` mocks all
  seams (NF-1); a real run is a separate live/eval concern.

## Lower-severity cleanups (Lane-C review findings, triage as a batch)
- `_default_ner` word-boundary `str.replace`; O(N²) per-candidate `FileVault._flush`;
  `_outbound_text`/`_redact_messages` duplication; `redact_fragments` dead code (prod uses
  `_redact_messages`); the contextvar opt-in footgun; concurrent temp-file race in `_flush`;
  duplicated vault-path expression in `_match_role`/`ingest`. Some fold naturally into c-005.

## Natural-language intake (c-006) follow-ups
- **A2 · Query-side negation → hard filters.** Parse "not Chennai" / "anyone but X" into a typed
  exclusion set enforced in the **pure-Python gate** (never the embedding). Mutates the frozen
  `OpenRole` (`exclude_*` fields) → its own ADR + `make contract-snapshot`. Deferred from c-006.
- **A4 · Bounded constraint-relaxation on no-match.** One explicitly-labelled deterministic pass,
  never an LLM loop (would breach the non-agentic invariant). Prefer near-misses as the primary
  answer (already shipped, AD-099/100). Needs its own ADR. Deferred from c-006.
- **Fully-deterministic Python relative-date resolver.** c-006 has the LLM resolve relative dates
  (today injected) + Python validate. A Python resolver for common patterns ("next month", "in N
  weeks") with the LLM extracting only the phrase is more robust — the natural fast-follow to AD-XXY.
- **NL-parse Tier-2/3 eval cassette.** c-006 unit-tests assembly with a fake predictor; a recorded
  cassette + a signature-shape regression (Tier 2) and a live smoke (Tier 3) would guard the real
  parse, mirroring clarify/score.
- **Offline MIPROv2 compile of `RoleIntakeSignature`.** The signature is a bare `dspy.Predict` with
  config-loaded instructions (compileable by construction). Compile against a golden set of real
  phrasings once enough are captured (§12 #12 of the interview).
- **NL `dsm explain --query`.** `explain` is `--role-id` only today; surfacing the NL front door
  through `explain` is a small follow-on.
- **Free-form `--query` unscanned-prose risk (FR-5-AC-2).** The intake call is `PseudonymisedLM`
  pass-through (no leak-scan, like clarify), trusting that role prose is non-PII. Free text invites
  an operator to type a candidate name — a wider surface than the structured CSV cell. If ever
  wrapped in `pii_context`, adopt `score.py`'s class-name `PIILeakError`-propagation discipline.

## Doc-foundation follow-ups (from the iteration-1 doc review)
- **Merge gate for the index.** The `docs/progress.md` AD-range is machine-checked
  (`tests/docs`), but the prose sections (Current status / Works end-to-end) still rely on
  `/handoff-index` at merge. Consider a CI nudge / PR-template enforcement (template added).
- **Architecture-doc maintenance policy.** `ee-*-architecture.md` now carry a Status header; keep
  them pointing at code+ADRs as they evolve rather than re-becoming authoritative-but-stale.
