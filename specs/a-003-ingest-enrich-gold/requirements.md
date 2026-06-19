# Requirements — a-003 Ingest Enrich, PII Boundary & Canonical-Entity Merge

> Slice 3 of ingestion (highest risk): silver (+ LLM enrichment) → **one canonical
> `GoldCandidate` per `candidate_id`** — cited, conflicts resolved, PII-clean, deterministic —
> plus snapshot reconciliation + tombstones. Builds on a-001 (bronze) and a-002 (silver +
> identity vault). **This is the first slice that calls an LLM**; the PII boundary becomes a hard
> gate here.
> References: `ee-ingestion-architecture.md` §5 (steps 4–5), §6 (Phase 4/5), §7 (merge + worked
> conflict), §9 (PII boundary), §10 (evidence-verify, determinism), §11 (versioned derivations),
> §12 (quality metrics); AD-010/AD-011/AD-032/AD-033/AD-060/AD-062/AD-068/AD-069/AD-070/AD-073;
> AD-030/AD-031 (score); `docs/tech.md` rules 1/2/4/5/6; `docs/product.md` invariants.

## User story

As the staffing engine, given the per-source `NormalizedRecord`s produced by a-002, I need each
consultant's unstructured profile (resume PDF) and feedback (markdown) **enriched by an LLM behind
the PII boundary** and then **merged with the supply silver into one canonical, cited
`GoldCandidate` per `candidate_id`** — with resume/feedback conflicts recorded (never averaged),
the cited feedback **facts** carried (the feedback *score* is computed downstream at match time, not
here), departed consultants tombstoned, and **no PII ever reaching the LLM** — so that the
downstream index/match phases work over one trustworthy, explainable, current-state entity per
consultant.

## Product invariants this slice must uphold

- **No bare LLM calls (CLAUDE.md rule 3 / AD-010).** Every external LLM call goes through
  `pii/PseudonymisedLM`. No raw prompt strings — DSPy typed `Signature`s only (tech.md rule).
- **No PII to OpenRouter, ever (AD-010/AD-068/AD-069).** Identity is redacted deterministically
  first, residual handled by NER, and an **outbound leak-scan is a hard gate** that blocks + fails
  the build/eval on any residual known-PII string. `name`/`email` never leave the vault boundary.
- **Every claim cites real evidence (product invariant; AD-040/AD-073).** Each extracted fact
  carries an `EvidenceCitation` whose quote is **verified verbatim-present in the source**; a
  fabricated/absent quote is rejected.
- **Determinism by default (tech.md rule 2 / §10/§11).** `temperature=0` + **versioned
  derivations** (`prompt_version`, `model_version`, `merge_version`). A model-version bump *is* a
  version bump (re-extract), never silent drift. **No response-caching layer in this slice** —
  determinism comes from temp=0 + versioning; caching is deferred unless a later slice needs it.
- **Provenance-weighted merge, never a blind union or average (§7).** Conflicts are recorded with
  both citations; **feedback > resume for demonstrated skill truth** (AD-033 spirit).
- **Gates stay LLM-free; ingest extracts facts, it does not score (rules 3/4; AD-030/AD-062).** An
  LLM never decides eligibility. The feedback **score** (and the 0.7·skill+0.3·feedback combine) is a
  **match/score** concern (Lane B) — ingest carries the cited feedback facts only, never a score.
- **Current-state correct (AD-070).** Latest snapshot wins; departed `candidate_id`s are
  tombstoned; a stale snapshot is flagged by the freshness guard.
- **Validate, log, skip — never silently wrong (§10/§12).** Failed extractions / coercions are
  logged with reason + payload + run_id, skipped at the right granularity, and counted in metrics.
- **Typed everything (rule: no dicts-as-contracts).** Every boundary is a Pydantic model.
- **Partial is better than absent (§7).** thin (CSV-only) / medium (CSV+resume) / rich
  (CSV+resume+feedback) profiles each yield a **valid** `GoldCandidate`.
- **PII layer is reusable, not ingest-specific.** `dsm/pii/` (`PseudonymisedLM`, `redact`,
  `leakscan`, `vault`) is a cross-cutting layer every LLM call site (ingest now; match/clarify,
  match/score later) reuses without an ingest import (AD-062 ownership).

## Acceptance criteria (EARS)

### PII boundary — redact (`dsm/pii/redact.py`)
- **PII-1** — WHEN profile/feedback text is prepared for the LLM, the system SHALL **first**
  deterministically remove the consultant's **known** identifiers (the `name` + `email` from the
  supply row), replacing each with a stable placeholder token, before any NER step (AD-069 order).
- **PII-2** — WHEN known identifiers are removed, the system SHALL then run NER (Presidio) over the
  residual text to catch **residual names + client-org names** and tokenize those too (AD-069). _(NER
  hardening — Indian-surname/org tuning + an org dictionary — is Lane C's to harden later; this slice
  ships the seam and a working default; see Sign-off item 2.)_
- **PII-3** — The redactor SHALL return both the redacted text and an **in-memory** placeholder→original
  mapping used only to de-anonymize the structured output; the mapping SHALL NOT be persisted or logged
  (tech.md "never log the pseudonym map").
- **PII-4** — The redactor's public surface SHALL be **generic** (`redact(text, *, known_pii)` /
  `deanonymize(text, mapping)`) with **no ingest-only assumptions**, so other lanes reuse it without
  importing `dsm.ingest`.

### PII boundary — leak-scan (`dsm/pii/leakscan.py`) — HARD GATE
- **PII-5** — WHEN any text is about to cross to the LLM (or, later, the embedder), the system SHALL
  run an **outbound leak-scan** for any residual **known-PII** string; WHEN a hit is found the system
  SHALL **block the call and fail the build/eval** (raise, non-zero) — never send the text (AD-069;
  eval invariant `no-PII-leak`).
- **PII-6** — The leak-scan SHALL be the single outbound choke point used by `enrich` (and reusable by
  any future call site); its surface SHALL be generic (`leak_scan(text, *, known_pii)` +
  `assert_no_leak(...)` raising a typed `PIILeakError`).

### Enrich — LLM extraction (`dsm/ingest/enrich.py`)
- **EN-1** — WHEN a silver record is a **resume** (`raw_text` present, `source_type=resume`), the
  system SHALL extract a `ProfileSummaryExtraction` (skills+proficiency, employers, projects, domains,
  seniority signals, education) via a **typed DSPy `Signature`** through `PseudonymisedLM` at
  `temperature=0` — **no raw prompt strings**.
- **EN-2** — WHEN a silver record is **feedback** (`source_type=feedback`), the system SHALL extract a
  per-item `FeedbackExtraction` (confirmed_skills, skill_gaps, domain_confirmation, sentiment,
  retention_requested, rejection_requested, summary) the same way.
- **EN-3** — The enrich path SHALL be **anonymize → leak-scan (gate) → LLM extract → de-anonymize**;
  the LLM SHALL only ever receive redacted text that passed the leak-scan (EN/PII order).
- **EN-4** — WHEN an extracted fact carries an `EvidenceCitation`, the system SHALL **verify the
  de-anonymized quote exists verbatim in the original source text**; WHEN it does not, the system SHALL
  **reject that fact** (drop it), log a citation-verify failure (reason + payload + run_id), and count
  it — without discarding the other valid facts in the same extraction (AD-073).
- **EN-5** — Every accepted extracted fact SHALL carry an `EvidenceCitation` (no unsourced facts reach
  gold) — product invariant "every claim cites real evidence".
- **EN-6** — The enrichment SHALL be **versioned**: each extraction is stamped with `prompt_version` +
  `model_version` (pinned in `config/`); a change to either is a **version bump** that forces
  re-extraction, never silent drift (§11). **No response cache is added this slice.**
- **EN-7** — WHEN the LLM returns output that fails the typed `Signature` schema (unparseable /
  type-invalid), the system SHALL log+skip+count that extraction (the record yields no enriched facts),
  never crash the run or pass an untyped dict (§10).
- **EN-8** — Unit/eval tests for enrich SHALL use **recorded LLM responses (cassettes)** — no live
  network/LLM call in the test/`make check` path (tech.md: no network in unit tests).

### Merge → gold (`dsm/ingest/merge.py`)
- **MG-1** — WHEN all silver + enriched records for one `candidate_id` are assembled, the system SHALL
  produce exactly **one** `GoldCandidate` keyed by that `candidate_id` (§5 step 5).
- **MG-2** — `grade`, `location`, `availability` SHALL take the **latest supply snapshot** as authority
  (operational system of record, §7); each emitted as a `Sourced[…]` with its supply citation.
- **MG-3** — Skill **names** SHALL be the **union** of all sources (widest recall); skill
  **proficiency** SHALL take **resume > CSV** (CSV carries none) (§7).
- **MG-4** — Skill **truth** (`MergedSkill.demonstrated`) SHALL take **feedback > resume**: feedback
  confirmation → `demonstrated=true`; feedback denial → `demonstrated=false`; no feedback signal →
  `demonstrated=None` (unverified) (§7; AD-033 spirit).
- **MG-5 (worked conflict, §7)** — WHEN a resume claims a skill (e.g. IaC/Terraform) **and** feedback
  denies it, the system SHALL set `demonstrated=false`, attach **both** citations to that
  `MergedSkill`, record the conflict in `MergedSkill.conflict` and in `GoldCandidate.conflicts`, and
  **SHALL NOT average or silently drop** either signal. _(A hard Terraform requirement then fails on
  real evidence, with a rationale showing why.)_
- **MG-6** — `domains` SHALL be resume claims as `Sourced[str]`; feedback confirmation SHALL **raise
  the confidence**, not overwrite (§7). `projects` SHALL come from the resume (only source of detail).
- **MG-7** — New-joiner-derived skills SHALL remain flagged `unverified` through to gold (carried from
  silver, AD-032); the enrichment/merge SHALL NOT silently promote them.
- **MG-8** — The merge input ordering SHALL be **deterministic** (sorted), so the same inputs +
  versions produce a byte-identical `GoldCandidate` (§5/§10).
- **MG-9** — The merge SHALL be stamped with `merge_version` (pinned); a logic bump is a version bump.

### Feedback facts on gold (NOT a score)
- **FB-1** — The system SHALL carry the aggregated **feedback facts** for a consultant onto the
  `GoldCandidate` as `list[FeedbackExtraction]` — each with `sentiment`, `retention_requested`,
  `rejection_requested`, `confirmed_skills`, `skill_gaps`, `domain_confirmation`, `summary`, and its
  **verified citation** — so match/score can derive the feedback term downstream.
- **FB-2** — The system SHALL NOT compute a feedback/track-record **score** at ingest. The feedback
  score (`CandidateAssessment.feedback_score`) and the `0.7·skill + 0.3·feedback` combine are a
  **match/score** concern (Lane B, AD-030/AD-062), computed at query time from these facts — see
  Sign-off item 3 (divergence from architecture §6's gold `performance_feedback_score` field).

### Reconcile + tombstones + freshness (`dsm/ingest/reconcile.py`)
- **RC-1** — Each run SHALL diff the **current** `candidate_id` set against the **prior** known set
  (from the prior `gold/` contents); WHEN a `candidate_id` is absent from the current snapshot, the
  system SHALL **tombstone** it (`is_tombstoned=true`) rather than delete it (AD-070).
- **RC-2** — The **latest snapshot SHALL win** for supply state (grade/location/availability) when a
  consultant moves between sheets across snapshots (AD-070; latest `valid_as_of`).
- **RC-3** — The system SHALL run an **as-of freshness guard**: WHEN the latest snapshot's
  `valid_as_of` is older than a configurable staleness threshold, it SHALL **warn**; the
  **refuse-vs-role-start** decision is enforced at match time (referenced, not built here) (AD-070; §10).
- **RC-4** — Tombstoned entities SHALL still be persisted (gold remains the audit record) and counted
  in run metrics (tombstones/run, §12).
- **RC-5** — Reconciliation SHALL be deterministic; the "current" set derives from this run's gold
  stream and the "prior" set from the on-disk `gold/` listing — no wall-clock dependence.

### Gold storage + change-detection (`dsm/ingest/goldstore.py`)
- **GS-1** — Each `GoldCandidate` SHALL be persisted to `gold/<candidate_id>.json`
  (`model_dump_json()`), immutable per content, written **atomically** (temp+rename), mirroring the
  bronze/silver writers (AD-066/§4).
- **GS-2** — Each `GoldCandidate` SHALL carry a `gold_hash` — a stable content hash (excluding the
  `gold_hash` field itself) — that the **index phase uses to detect change** and re-embed only what
  changed (§11; out of scope to consume here, in scope to emit).
- **GS-3** — `gold/` SHALL be **gitignored** (it carries `name_vault_ref`/`email_vault_ref` and
  verbatim evidence quotes that may include client-org names) — consistent with bronze/silver PII
  handling (§9 defense-in-depth).
- **GS-4** — Gold SHALL NOT contain raw `name`/`email`: identity is carried as
  `name_vault_ref`/`email_vault_ref` only (AD-068).

### Partial-profile tolerance
- **PP-1** — WHEN a consultant has a supply row but **no resume and no feedback** (thin), the system
  SHALL still emit a valid `GoldCandidate` (skills proficiency `None`, `demonstrated=None`,
  new-joiner skills `unverified`, empty `feedback` list).
- **PP-2** — WHEN a consultant has supply + resume but no feedback (medium), the system SHALL emit a
  valid `GoldCandidate` with resume-enriched skills/projects/domains and `demonstrated=None`.
- **PP-3** — WHEN a consultant has supply + resume + feedback (rich), the system SHALL emit a valid
  `GoldCandidate` with `demonstrated` set where feedback speaks, conflicts recorded, and the cited
  feedback facts carried (FB-1).

### Lineage / quality metrics (`dsm/ingest/lineage.py` extend, §12)
- **LN-1** — The run SHALL emit quality metrics: **conflict rate**, **leak-scan hits**,
  **citation-verify failures**, **tombstones/run**, and **profile coverage** (thin/medium/rich counts).
- **LN-2** — Every leak-scan hit, citation-verify failure, and merge conflict SHALL be logged via
  `lineage` (reason + payload + run_id) and counted — none silently swallowed (§10/§12).
- **LN-3** — Metrics SHALL be derived from the gold/event stream (deterministic), not a global mutable
  counter (mirrors a-002 `count_unmapped_skills`).
- **LN-4** — `leak-scan hits` is an **invariant**: a non-zero count SHALL fail the build/eval (it means
  PII almost reached the LLM) — consistent with PII-5.

### Determinism, versioning, contracts (non-functional)
- **NF-1** — Determinism is bounded, and the boundary is the LLM step (the only stochastic input):
  - **(a) All non-LLM stages SHALL be byte-identical by construction** — redact, merge authority,
    conflict resolution, `gold_hash`, persistence — given the same inputs
    and version stamps (sorted iteration; no clocks/RNG; injected `today`).
  - **(b) The LLM extraction SHALL be pinned in tests by recorded cassettes** (EN-8), so the
    test/`make check` path produces **byte-identical** gold (this is what NF-1's tests assert).
  - **(c) This slice adds NO response cache** (EN-6) — so a **live** run is **not** guaranteed
    byte-identical. `temperature=0` reduces model drift but does not by itself guarantee token-identical
    output; the version stamps (NF-2) guarantee only that any change is a **visible version bump**, never
    silent drift. Hardening live reproducibility (e.g. a content+version derivation cache, §11) is
    explicitly **out of scope** here.
- **NF-2** — A `model_version` (or `prompt_version`) change SHALL force re-extraction (version bump),
  never silent drift; the stamps live on the gold entity for lineage (§11).
- **NF-3 (IMPORT — Sign-off item 2)** — The ingest import contract SHALL be relaxed so `dsm.ingest`
  MAY import the PII boundary (`dsm.pii.pseudonymised_lm`, `dsm.pii.redact`, `dsm.pii.leakscan`, and
  the already-permitted `dsm.pii.vault`) **and** `dspy` — the sanctioned LLM path. `dsm.ingest` SHALL
  remain forbidden from `dsm.match`, `dsm.index`, `modal`, and `httpx` (no direct provider access; the
  provider is reached only *through* `PseudonymisedLM`). The relax is a single contract edit in its own
  commit with a note → ADR-078.
- **NF-4 (FROZEN — Sign-off item 1)** — The only frozen-contract (`dsm/models.py`, AD-060) edit this
  slice is the **agreed `EvidenceCitation` relaxation** (add optional `source_hash`/`locator`; `text`
  is the AD-073 verified verbatim quote) — backwards-compatible, via superseding ADR-077. **Any other
  frozen-contract change is out of scope and would need its own ADR + a STOP.**

## Out of scope (do not build here)
Embedding / `embed_text` / `skill_set` / Milvus upsert (the **index** phase); query-time reranking;
scoring / ranking / `CandidateAssessment`; the encrypted-at-rest vault implementation (Lane C hardens
`vault.py` later — this slice still uses the in-memory seed for `*_vault_ref`); **LLM response caching**
(deferred unless a later slice shows it is needed); the refuse-vs-role-start half of the freshness
guard (enforced at match time); Presidio NER model/dictionary hardening for org names (Lane C); **the
feedback/track-record `score` and the `0.7·skill+0.3·feedback` combine** — a match/score concern
(Lane B, AD-030); ingest carries the cited feedback *facts* only (FB-1/FB-2).

## Sign-off items (decide at spec review, per CLAUDE.md golden rule 1)

> Each item is framed with a recommendation. Items 1–2 follow precedents already set (AD-075/AD-076 in
> a-002). Item 3 is a deliberate divergence from the signed-off architecture §6 (feedback score moves to
> match/score). **Nothing below is coded until the whole spec is signed off.**

1. **Frozen-contract edit — `EvidenceCitation` relaxation (pre-agreed).**
   **Recommend → relax** frozen `dsm/models.py::EvidenceCitation`: add `source_hash: str | None = None`
   and `locator: str | None = None`; document `text` as the AD-073 **verified verbatim quote**; keep
   `source: EvidenceSource` and `metadata`. Backwards-compatible (new fields optional) → **one citation
   type system-wide** (ingest + serving). Superseding **ADR-077**, own commit. This is the *only*
   sanctioned frozen edit this slice (NF-4). _Open question for sign-off: confirm no second frozen edit
   is needed (the gold entity is the new ingest-local `GoldCandidate`, not a `dsm.models.Candidate`
   edit — see design.md "Naming & frozen-contract collisions")._

2. **PII layer placement + import-contract relax (the high-risk item — CLAUDE.md "weaken the PII
   boundary" / "crosses Lane C ownership" STOP triggers).**
   `enrich.py` (in `dsm.ingest`) must route through `PseudonymisedLM` + `redact` + `leakscan` and use
   `dspy` — but today's contracts forbid `dsm.ingest → dspy` and `dsm.ingest → dsm.pii.*` (except
   `vault`). **Recommend → option (b), mirroring a-002's vault pattern:** (i) add **generic** `redact.py`
   + `leakscan.py` under `dsm/pii/` (Lane C dir, AD-062) with a reusable, ingest-free surface; **Lane A
   seeds** a working default, **Lane C hardens** Presidio/org-dictionary later; (ii) **relax NF-3** so
   `dsm.ingest` may import the PII boundary modules + `dspy`, while `modal`/`httpx`/`dsm.match`/
   `dsm.index` stay forbidden. This **routes through** the boundary (the sanctioned path) — it does not
   weaken it; the leak-scan becomes a hard gate. Superseding **ADR-078**. **Requires Lane C's explicit
   agreement** (recorded in ADR-078) to (i) the new modules under their directory and (ii) Lane A
   seeding them. _Alternative (a): expose a single `dsm.pii.enrich_through_boundary(signature, text,
   known_pii)` facade so ingest never imports `dspy` directly — rejected as the default because the
   DSPy `Signature`s are ingest-domain extraction contracts and belong with the enrich code; the facade
   would invert ownership awkwardly. Flagged for the reviewer to override if preferred._

3. **Feedback *score* deferred to match/score — divergence from architecture §6.**
   Architecture §6 lists `performance_feedback_score: float | None` on the gold entity. **Recommend →
   do NOT compute it at ingest this slice; carry the cited feedback *facts* only (FB-1).** Rationale:
   (i) the frozen serving contract already has `CandidateAssessment.feedback_score` +
   `combined_score` — a gold score would be **redundant**; (ii) score computation is the **match/score**
   Python step (AD-030, tech.md rule 4, Lane B per AD-062) — computing it at ingest crosses the lane
   boundary; (iii) the §3.4 recency-decay formula is **unconfirmed** (PRD not in this repo) and baking an
   unsettled formula into the **immutable gold layer** means a constant tweak forces a full re-merge,
   whereas in match/score it is a `config/` change with no re-ingest. **Recorded as ADR-079** (supersedes
   the §6 gold-field placement: *feedback facts in gold, feedback score in match/score*). _Decision
   needed: confirm this divergence from the signed-off architecture, or instruct that the precomputed
   role-invariant number stay on gold (in which case the §3.4 curve + whether feedback items carry dates
   must also be settled here)._

> Spec sign-off (this whole document) is required before any code (CLAUDE.md golden rule 1). The three
> items above are the explicit decisions the reviewer must confirm or amend.
