# Tasks — Lane B · Clarify (b-001-clarify)

> Each task = one commit. Imperative message referencing the spec, e.g. `feat(clarify): ClarifyRole DSPy signature per b-001`.
> Run `make check` after each task. Never commit red.

## Task list

### B-001 · DSPy signature + LM wiring
**AC:** AC-B07, AC-B08  
Define `ClarifyRole(dspy.Signature)` in `dsm/match/clarify.py`. Instantiate `PseudonymisedLM` from config (`reasoning_model`, `temperature=0`). Wire `dspy.configure(lm=...)`. No predict call yet — module imports clean.  
**Test:** `tests/match/test_clarify.py` — import succeeds; `PseudonymisedLM` is the configured LM type.

---

### B-002 · Deterministic fallback parser
**AC:** AC-B10, AC-B11  
Implement `_fallback_parse(role: OpenRole) -> TargetProfileScorecard` as a standalone pure function. Regex markers `(expert|depth)` → HARD; `(nice to have|desired)` → DESIRED; else → HARD. Sets `clarification_notes` containing `"fallback=true"`.  
**Test:** unit test with mock roles covering: marker in description, marker in skill name, composite location `"Bengaluru / remote-India"`, empty description, empty skills.

---

### B-003 · `clarify_role` predict + Pydantic parse
**AC:** AC-B01, AC-B02, AC-B03, AC-B04, AC-B05, AC-B12, AC-B13  
Implement the main `clarify_role(role: OpenRole) -> TargetProfileScorecard` calling `dspy.Predict(ClarifyRole)`. Parse JSON output fields into `TargetProfileScorecard`. Enforce hard/desired mutual exclusion post-parse (AC-B13). Mock the LM in tests to return pre-canned JSON; assert scorecard fields.  
**Test:** happy-path golden fixture for ROLE-01 (mock LM returns correct JSON) → `hard_depth_skills` contains `kotlin/HARD`.

---

### B-004 · Retry-on-validation-failure
**AC:** AC-B09, AC-B10  
Wrap the predict call: catch `ValidationError`, append error text to the `description` input, retry once. On second failure, call `_fallback_parse`. Log (structured, no raw LLM text) which path was taken.  
**Test:** mock LM that returns bad JSON on first call, good JSON on second → assert no `clarify_degraded`; mock LM that always returns bad JSON → assert `fallback=true` in `clarification_notes`.

---

### B-005 · Golden fixtures for seed roles (mock LM)
**AC:** AC-B06 (ROLE-01 seed invariant)  
Add `tests/match/fixtures/roles/` with `(input, expected)` pairs for the seed roles (start with ROLE-01 and ROLE-02; new roles can be added as a fixture file drop with no test code changes). Each test drives a mock LM returning the expected scorecard JSON and asserts field-level equality. Add `DSM_LIVE_LM=1` guard to run the same suite against the real LM (Promptfoo-ready).  
**Test:** parametrised over all fixture files found in the directory; ROLE-01 asserts `kotlin` in `hard_depth_skills`.

---

### B-006 · Config integration + harness green
**AC:** all  
Read `reasoning_model` and `availability_window_days` from `config/default.yaml` (add keys if absent). Confirm `make check` is green. Update `docs/progress.B.md` via `/handoff`.  
**Test:** `make check` passes (format, lint, pyright, all tests, import contracts).
