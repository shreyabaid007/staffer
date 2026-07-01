# c-010 — Tasks (one task = one commit)

Ordered, atomic, each mapped to acceptance criteria. `make check` green before each commit.

- [x] **T-000 — ADR + docs.** Ratify **AD-XXX** (eval hardening) in `docs/decision.md`; add an eval
  line to `docs/tech.md` § Stack; refresh `dsm/eval/README.md` (new tiers + NIST AI RMF mapping);
  tick the C1/C6 items in `docs/backlog.md`. *(NF-1, NF-2; gates the code below.)*

- [x] **T-001 — Guardrail-detector validation.** `dsm/eval/guardrail_validation.py`:
  `detector_metrics` (P/R/F1/TPR/TNR + `cohens_kappa`), `sweep_threshold`, `best_threshold`
  (F1/Youden's J). Pure. *(FR-1-AC-1/2.)*

- [x] **T-002 — Red-team module + corpus.** `dsm/eval/red_team.py` (`contains_injection`, ASR,
  report) + `tests/fixtures/injection_corpus.json` (signed off). *(FR-2-AC-1/2.)*

- [x] **T-003 — Fairness module.** `dsm/eval/fairness.py` (`parity`, `aggregate_parity`). Pure.
  *(FR-3-AC-1/2.)*

- [x] **T-004 — Fixture loaders.** `dsm/eval/hardening_fixtures.py` (signed-off-gated loaders) +
  `tests/fixtures/guardrail_corpus.json`. *(FR-1, FR-2.)*

- [x] **T-005 — Test tiers.** `tests/eval/test_guardrail_validation.py`,
  `tests/eval/test_red_team.py`, `tests/eval/test_fairness.py` — offline metric units + pipeline
  tiers + skip-gated live tiers. *(all FR ACs, FR-1-AC-3, FR-2-AC-3, FR-3-AC-2.)*

- [ ] **T-006 — Harness + handoff.** `make check` green (unchanged scope) + `make eval` green
  (offline pass; live skip cleanly); update `docs/progress.C.md`. *(Definition of Done.)*

## Done when
Guardrail detectors are scored as classifiers with a calibration sweep · injection corpus yields
ASR=0 through the guarded pipeline with no narrative leak · deterministic layer proven proxy-blind +
live sub-score parity within tolerance · all tiers additive to `make eval` · `make check` green and
unchanged in scope · new decisions in `docs/decision.md` · `docs/progress.C.md` updated.
