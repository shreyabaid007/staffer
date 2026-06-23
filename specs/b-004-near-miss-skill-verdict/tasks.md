# Tasks — b-004 Near-Miss Skill Verdict + Selection Rationale

> Ordered, atomic, one task = one commit. Each maps to an acceptance criterion. Stop for human
> sign-off on this spec before T-001. **T-000 ratifies AD-098, a frozen-contract amendment
> (AD-060) — it needs explicit team sign-off at the gate.**

> **Post-sign-off rework (AD-099).** After Part 1 landed, the labelling approach (AD-097) was
> replaced by **filtering**: skill-failers are excluded from near-misses, not annotated. AD-099
> supersedes AD-097 (and AD-088's hard-skill near-misses). Part 1's tasks below describe the
> original labelling; the shipped behaviour is the AD-099 filter (one follow-up commit reworked
> `build_near_misses` + tests; decision.md carries AD-099).

## T-000 — ADRs

- [ ] **T-000-ADR** — Append **AD-097** (skill verdict; no model change) and **AD-098**
  (`NearMiss.selection_rationale` field + LLM rationale seam; AD-060 amendment) to
  `docs/decision.md`.
  *Commit:* `docs(decision): ratify AD-097 + AD-098 near-miss verdict & rationale`.

## Part 1 — hard-skill verdict (deterministic, no model change)

- [ ] **T-001 — Compute the skill verdict.** In `build_near_misses`, collect the
  `AVAILABILITY_MISMATCH` + `LOCATION_MISMATCH` candidates and run them through
  `exact_hard_skill_filter` once → a `clears_skills` email set. No wording change yet.
  → FR-3, FR-4, FR-5. *Commit:* `feat(near-miss): compute hard-skill verdict via exact filter per AD-097`.

- [ ] **T-002 — Render verdict on availability near-misses.** Append `"; clears all hard skills"` /
  `"; also missing {n} hard skill(s): {names}"` to the availability `gap_summary`.
  → FR-1. *Commit:* `feat(near-miss): surface skill verdict on availability misses`.

- [ ] **T-003 — Render verdict on location near-misses.** Append the same suffix to the location
  `gap_summary`.
  → FR-2. *Commit:* `feat(near-miss): surface skill verdict on location misses`.

- [ ] **T-004 — Sub-order availability misses by skill clearance.** Widen sort keys to the uniform
  `(type_rank, sub_rank, metric, email)` 4-tuple; availability-clears ranks above availability-gap.
  Cross-type order + top-3 cap unchanged.
  → FR-6. *Commit:* `feat(near-miss): rank skill-clearing availability misses first per AD-097`.

## Part 2 — selection rationale (LLM via PseudonymisedLM)

- [ ] **T-005 — Add the `NearMiss.selection_rationale` field.** Additive, optional, defaulted
  `str | None = None` in `dsm/models.py`. Confirm all existing constructions still validate.
  → FR-9-AC-1. *Commit:* `feat(models): add NearMiss.selection_rationale per AD-098`.

- [ ] **T-006 — Rationale predictor + prompt.** Add `NearMissRationalePredictor` alias,
  `make_near_miss_rationale_predictor(lm)` (DSPy `Signature` over `PseudonymisedLM`, PII-free
  inputs), and an `explain_near_miss`/applier with skip-on-error, in `dsm/match/score.py` (or new
  `dsm/match/near_miss.py`). Add the `near_miss_rationale` prompt to `config/prompts/*` +
  `config/default.yaml`.
  → FR-9-AC-3, FR-9-AC-4, FR-10-AC-1. *Commit:* `feat(near-miss): rationale predictor over PseudonymisedLM`.

- [ ] **T-007 — Wire the seam through the no-match path.** Add `near_miss_predict=None` to
  `run_match`; in `_no_match`, after ordering + the `[:3]` cap, apply the predictor and
  `model_copy` each shown near-miss with its rationale. Build the live predictor in `_match_role`
  at the CLI edge.
  → FR-9-AC-2, FR-10-AC-2, FR-8. *Commit:* `feat(near-miss): attach LLM rationale to shown near-misses`.

## Tests + close-out

- [ ] **T-008 — Tests + invariants.** Add the design.md eval cases to `tests/cli/test_no_match.py`:
  Part 1 (FR-1, FR-2, FR-5, FR-6, below-floor, determinism), Part 2 (rationale set via fake
  predictor; ≤3 invocations; skip-on-error → `None`; PII-free inputs). Confirm existing
  `test_no_match` / `test_orchestrator` / `test_explain` assertions still pass.
  → FR-1…FR-10. *Commit:* `test(near-miss): cover skill verdict + rationale (b-004)`.

## Part 3 — closest-on-skills (AD-100)

- [ ] **T-010 — ADR.** Append **AD-100** (`NoMatchResult.closest_on_skills`) to `docs/decision.md`;
  bump the footer to AD-099. *Commit:* `docs(decision): ratify AD-100 closest-on-skills section`.

- [ ] **T-011 — Shared structured gap helper.** In `dsm/index/retrieve.py`, add `HardSkillGap` +
  public `hard_skill_gap(...)`; `_hard_skill_gap` (string) + `exact_hard_skill_filter` reuse it (no
  behaviour change). Add a `hard_skill_gap` unit test; `test_retrieve.py` stays green.
  → FR-12-AC-2. *Commit:* `refactor(retrieve): extract structured hard_skill_gap helper`.

- [ ] **T-012 — `NoMatchResult.closest_on_skills` field.** Additive, optional, defaulted in
  `dsm/models.py`. → FR-11 (model), NF-3. *Commit:* `feat(models): add NoMatchResult.closest_on_skills per AD-100`.

- [ ] **T-013 — `build_closest_on_skills` + wiring.** New builder (HARD_SKILL_MISMATCH → ordered
  `NearMiss` via `hard_skill_gap`); `_no_match` builds + caps + rationale-annotates both lists;
  `_lineage` dumps the section.
  → FR-11, FR-12, FR-13. *Commit:* `feat(no-match): surface closest-on-skills with rationale + lineage`.

- [ ] **T-014 — Tests.** Design.md Part 3 eval cases across `tests/cli/test_no_match.py`,
  `tests/cli/test_orchestrator.py`, `tests/index/test_retrieve.py`; near-miss (AD-099) tests stay
  green. → FR-11…FR-13. *Commit:* `test(no-match): cover closest-on-skills section`.

- [ ] **T-009 — Verify + handoff.** `make check` green; update `docs/progress.B.md` via `/handoff`.
  → Definition of Done. *Commit:* none (handoff edits the lane file).

## Done when

All FR acceptance criteria met · `make check` green · new behaviour covered in
`tests/cli/test_no_match.py` · AD-097 + AD-098 in `docs/decision.md` · `docs/progress.B.md`
updated · gates / exact filter / scoring math / ranking unchanged (FR-8 / NF-4) · the only
`dsm/models.py` change is the additive `NearMiss.selection_rationale` (NF-3).
