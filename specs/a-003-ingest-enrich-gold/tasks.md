# Tasks — a-003 Ingest Enrich, PII Boundary & Canonical-Entity Merge

> Ordered, atomic, independently testable. **One task = one commit**, imperative, referencing the
> spec/ADR. `make check` green before every commit. Each task maps to ≥1 acceptance criterion.
> **All code below is BLOCKED until the three Sign-off items in `requirements.md` are resolved and
> the whole spec is approved (CLAUDE.md golden rule 1).**

## Sign-off gate (resolve before any code)
- **S-1** — Confirm the `EvidenceCitation` relaxation (ADR-077) is the *only* frozen edit (NF-4).
- **S-2** — Confirm option (b): generic `dsm/pii/redact.py`+`leakscan.py` (Lane A seeds, Lane C
  hardens) + the NF-3 import relax (ADR-078). **Get Lane C's explicit agreement** (recorded in ADR-078).
  Confirm adding **Presidio + spaCy** to `pyproject`/lockfile is acceptable (already in tech.md).
- **S-3** — Confirm the divergence from architecture §6: **feedback *facts* in gold, feedback *score*
  in match/score** (ADR-079) — `performance_feedback_score` and `score_feedback.py` are dropped from
  this slice; the §3.4 formula moves to the Lane B scoring spec.

## Implementation (after whole-spec sign-off)

- **T-000-ADR — Record ADRs** → append **AD-077** (EvidenceCitation relax), **AD-078** (ingest→pii+dspy
  import relax + redact/leakscan placement, Lane C agreement noted), **AD-079** (feedback facts in gold,
  feedback score deferred to match/score — supersedes §6 field placement) to `docs/decision.md`.
  _(AD-060 process; precedes the edits they authorize.)_

- **T-001-FROZEN — Relax `EvidenceCitation`** → edit `dsm/models.py`: add `source_hash: str | None =
  None`, `locator: str | None = None`; document `text` as the verified verbatim quote. Own commit
  (`refactor(models): relax EvidenceCitation with source_hash/locator per AD-077`). Verify `make check`
  green (no gates/score/rank regressions). _(NF-4; AD-077; S-1)_

- **T-001-IMPORT — Relax NF-3 import contract** → `pyproject.toml`: drop `dspy` from the
  ingest-forbidden contract; drop `dsm.pii.pseudonymised_lm` from the ingest→pii forbidden contract
  (leaving all `dsm.pii` permitted to ingest); keep `dsm.match`/`dsm.index`/`modal`/`httpx` forbidden.
  Own commit + note → AD-078. Verify import-linter green. _(NF-3; AD-078; S-2)_

- **T-002 — PII redact (generic, Lane C dir — Lane A seeds)** → `dsm/pii/redact.py`:
  `redact(text, *, known_pii)` (deterministic known-PII pass first, then a mockable Presidio NER seam),
  `deanonymize(text, mapping)`, `RedactionResult`. Add Presidio+spaCy to `pyproject` (NER seam offline-
  mockable). Tests: PII-1 (known removed), PII-2 (NER residual via mock), PII-3 (mapping not
  persisted/logged), PII-4 (no ingest import). _(PII-1..4; AD-078)_

- **T-003 — PII leak-scan hard gate (generic, Lane C dir — Lane A seeds)** → `dsm/pii/leakscan.py`:
  `leak_scan`, `assert_no_leak`, `PIILeakError`, `LeakScanResult`. Tests: PII-5 (residual hit raises +
  non-zero), PII-6 (clean passes), hits logged as a **count**, never the value. _(PII-5/6; LN-4)_

- **T-004 — Phase-4/5 ingest models** → extend `dsm/ingest/models.py` with `Sourced[T]`,
  `SkillExtraction`, `ProfileSummaryExtraction`, `FeedbackExtraction`, `MergedSkill`, `GoldCandidate`
  (import frozen `EvidenceCitation`/`Location`/`AvailabilityState`/`ProficiencyLevel`; reuse a-002
  `Grade`/`Confidence`/`NormalizedSkill`). Tests: instantiation, frozen, generic `Sourced`, partial
  `GoldCandidate` (all-optional supply fields). _(§6; naming-collision resolution)_

- **T-005 — Versioned prompts + config** → `config/prompts/` (profile_extraction, feedback_extraction
  instruction text); `config/default.yaml`: `enrich.prompt_version`, `reconcile.max_staleness_days`.
  No code path yet beyond loading. Tests: config loads; versions present. _(EN-6; RC-3; tech.md rule 6)_

- **T-006 — Enrich (DSPy signatures + anonymize→gate→LLM→deanon→verify)** → `dsm/ingest/enrich.py`:
  `ProfileExtraction`/`FeedbackExtractionSig` signatures; `enrich_resume`/`enrich_feedback` through
  `PseudonymisedLM` at temp 0; leak-scan gate before the call; de-anonymize output; **verify every
  citation quote verbatim-present** (drop+log+count absent facts); skip+count schema-invalid output;
  stamp `prompt_version`/`model_version`. **Cassette-recorded tests only.** Tests: EN-1, EN-2, EN-3,
  EN-4 (case 7), EN-5, EN-7. _(EN-1..8; PII-5)_

- **T-007 — Merge → gold + authority/conflict** → `dsm/ingest/merge.py`: `merge_candidate`/`merge_run`;
  §7 authority (grade/loc/avail latest supply; names union; proficiency resume>CSV; demonstrated
  feedback>resume); **MG-5 worked conflict** (both citations, `conflict` set, never averaged);
  new-joiner `unverified` carried; **carry the cited `FeedbackExtraction` facts (FB-1) — no feedback
  score computed (FB-2, AD-079)**; deterministic sorted builds; `merge_version`. Tests: MG-1..MG-9
  (esp. case 9 §7), FB-1/FB-2 (cases 13/14), PP-1/2/3. _(MG-*; FB-*; PP-*)_

- **T-008 — Gold store + `gold_hash` + gitignore** → `dsm/ingest/goldstore.py`: `gold_hash` (excludes
  itself, sorted), atomic `write_gold` (`gold/<cid>.json`), `read_gold`, `list_gold_ids`; add `gold/*`
  (+`.gitkeep`) to `.gitignore`. Tests: GS-1/GS-2 round-trip + hash stable/change-sensitive, GS-4 (no
  raw name/email). _(GS-*)_

- **T-009 — Reconcile + tombstones + freshness** → `dsm/ingest/reconcile.py`: `reconcile(current,
  prior)`, `freshness_guard(valid_as_of, *, max_staleness_days, today)`, `ReconcileResult`. Wire
  tombstone flip on carried-forward gold. Tests: RC-1 (case 15), RC-2 (case 16), RC-3 (case 17), RC-5
  determinism. _(RC-*)_

- **T-010 — Lineage quality metrics** → extend `dsm/ingest/lineage.py`: `log_leak_block`,
  `log_citation_verify_failure`, `log_conflict`, `log_tombstone`; counters `count_conflicts`,
  `count_citation_failures`, `coverage(gold)`, `count_tombstones`. Deterministic, stream-derived. Tests:
  LN-1/LN-2/LN-3; **LN-4** (non-zero leak hits fail). _(LN-*; §12)_

- **T-011 — Cassettes + end-to-end enrich→gold test** → `tests/fixtures/ingest/enrich/` +
  `tests/fixtures/ingest/cassettes/`; drive silver→enrich→merge→reconcile→gold over thin/medium/rich
  synthetic profiles incl. the §7 conflict; assert the full design.md case table. Fixed
  `DSM_CANDIDATE_ID_KEY`; **no network/LLM**. Tests: NF-1 (byte-identical), NF-2 (version bump
  re-extract), cases 18/21/22. _(end-to-end acceptance; NF-1/2)_

- **T-012 — Wire `dsm ingest` (Lane C dir)** → edit `dsm/cli/commands.py`: after parse+silver, run
  enrich→merge→reconcile→`write_gold` per candidate; add `--gold-dir`; print the PII-safe `── Gold ──`
  summary (entities, coverage split, conflicts, citation-verify failures, **leak hits=0**, tombstones,
  freshness warnings). **No raw_text/name/email/quote to stdout**; leak hit or unexpected exception →
  exit non-zero; expected invalid data counted, exit 0. Tests: CLI summary correct + **PII-safe**
  (assert no raw_text/name/email/quote in stdout); leak-hit → exit 1. _(CLI; PII-5; case 23)_

- **T-013 — Verify contracts + docs refresh** → confirm revised NF-3 import contract green (case 24);
  confirm `make check` green end-to-end; refresh `docs/progress.A.md` via `/handoff`; fix any spec line
  that drifted in the same PR. Flag the cross-lane touches at PR (Lane C files: `dsm/models.py`,
  `dsm/pii/redact.py`, `dsm/pii/leakscan.py`, `dsm/cli/commands.py`). _(NF-3; CLAUDE.md refresh rule)_

## Definition of done
All acceptance criteria in `requirements.md` met · `make check` green · each new behaviour has a test
(cassette-based for LLM paths) · AD-077/AD-078/AD-079 in `docs/decision.md` · `docs/progress.A.md`
updated via `/handoff`. **Out of scope (later slices):** embedding/`skill_set`/Milvus upsert; query-time
rerank; scoring/ranking; encrypted-at-rest vault; LLM response caching; refuse-vs-role-start freshness;
Presidio org-dictionary hardening.
