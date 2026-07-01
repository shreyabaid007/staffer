# c-009 — Tasks (one task = one commit)

Ordered, atomic, each mapped to acceptance criteria. `make check` green before each commit.

- [ ] **T-000 — ADR + deps + config.** Ratify **AD-XXX** (Guardrails AI input/output validation
  layer) in `docs/decision.md`; add `guardrails-ai` to `pyproject.toml`; add `guardrails:` section
  to `config/default.yaml` (all validators enabled by default); add "Guardrails" line to
  `docs/tech.md` § Stack. Use the `AD-XXX` placeholder (resolved at merge via `/handoff-index`).
  *(NF-2, NF-4; gates all code below.)*

- [ ] **T-001 — Input guard module.** `dsm/guardrails/__init__.py` +
  `dsm/guardrails/input_guard.py`: `build_input_guard(config)` → `Guard | None`;
  `validate_input(guard, text, candidate_id)` → raises `InputRejectedError` on injection.
  Install `guardrails/detect_jailbreak` hub validator. Log rejections with `candidate_id` only.
  *(FR-1-AC-1, FR-1-AC-2.)*

- [ ] **T-002 — Output guard module.** `dsm/guardrails/output_guard.py`:
  `build_score_guard(config)` → deterministic `[0.0, 1.0]` clamping via `valid_range`;
  `build_grounding_guard(config)` → `bespoke_minicheck` sentence-level grounding;
  `validate_scores()` → clamp + log; `ground_narrative()` → filter ungrounded sentences + log.
  Install `guardrails/valid_range` and `bespokelabs/bespoke_minicheck` hub validators.
  *(FR-2-AC-1, FR-2-AC-2, FR-3-AC-1, FR-3-AC-2.)*

- [ ] **T-003 — Narrative guard module.** `dsm/guardrails/narrative_guard.py`:
  `build_narrative_guard(config)` → composed `bias_check` + `toxic_language` guard;
  `validate_narrative()` → reask on bias/toxicity, fallback to exception + flag.
  Install `guardrails/bias_check` and `guardrails/toxic_language` hub validators.
  *(FR-4-AC-1 … FR-4-AC-5.)*

- [ ] **T-004 — Composition root wiring.** Wire the three guard modules into `dsm/cli/commands.py`:
  wrap `_build_score_predictor` with the guarded predictor pattern; wrap
  `_build_resume_predictor` / `_build_feedback_predictor` with the input guard; wrap
  `_build_near_miss_rationale_predictor` with the narrative guard. All guards read config and
  no-op when disabled. `dsm/match` does NOT import `dsm/guardrails`.
  *(FR-1-AC-3, FR-1-AC-4, NF-1.)*

- [ ] **T-005 — Tests.** `tests/guardrails/test_input_guard.py` (planted injection → rejected;
  clean text → passes; disabled → no-op); `tests/guardrails/test_output_guard.py` (score 1.5 →
  clamped; score 0.7 → unchanged; grounding strips ungrounded sentence — `make eval` only);
  `tests/guardrails/test_narrative_guard.py` (biased text → flagged — `make eval` only; clean
  → passes); `tests/guardrails/test_imports.py` (AST boundary: `dsm.guardrails ⊥ dsm.match`,
  `dsm.guardrails ⊥ dsm.pii`).
  *(NF-3, NF-5, all FR ACs.)*

- [ ] **T-006 — Harness + handoff.** `make check` green (deterministic guard tests pass, import
  boundary holds); update `docs/progress.C.md` via the lane handoff.
  *(Definition of Done.)*

## Done when
All FR acceptance criteria met · input guard rejects planted injection text · score bounds clamp
out-of-range values · grounding filters ungrounded sentences · bias/toxicity screens narratives ·
all guards independently toggleable via config · `dsm.match ⊥ dsm.guardrails` import boundary
holds · `make check` green · new decisions in `docs/decision.md` · `docs/progress.C.md` updated.
