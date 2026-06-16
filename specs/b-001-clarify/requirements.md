# Requirements — Lane B · Clarify (b-001-clarify)

> Owner: Eng B (Reasoning lane). Scope: replace `dsm/match/clarify.py` stub with a real DSPy module.

## User story
As a staffing manager running `dsm match`, I want the system to parse the raw role text into a structured `TargetProfileScorecard` so that downstream gates and scoring operate on typed, unambiguous skill requirements rather than free text.

## Acceptance criteria (EARS)

**Happy path**

- **AC-B01** WHEN `clarify_role` receives an `OpenRole` whose `required_skills` contain a `SkillRequirement` with name `"kotlin"` and depth `HARD`, the returned `TargetProfileScorecard.hard_depth_skills` SHALL contain that skill.
- **AC-B02** WHEN the role `description` or `required_skills` name text contains an inline marker `"(expert)"` or `"(depth)"`, the system SHALL map that skill to `SkillDepth.HARD` in `hard_depth_skills`.
- **AC-B03** WHEN the role text contains an inline marker `"(nice to have)"` or `"(desired)"`, the system SHALL map that skill to `SkillDepth.DESIRED` in `desired_skills`.
- **AC-B04** WHEN the role has `location.city` = `"Bengaluru"` and `description` or `required_skills` text references `"remote-India"`, the `TargetProfileScorecard.location` SHALL have `remote_eligible=True`.
- **AC-B05** WHEN `clarify_role` is called, the returned scorecard SHALL have `availability_window_days = 14` (from config, AD-021).
- **AC-B06** WHEN `clarify_role` is called with ROLE-01, the scorecard SHALL contain `"kotlin"` in `hard_depth_skills` (depth=HARD) AND `"kotlin"` SHALL NOT appear only in `desired_skills`. *(seed eval invariant)*

**PII / LLM boundary**

- **AC-B07** WHEN calling the LLM, the module SHALL obtain the LM exclusively through `pii.PseudonymisedLM`; no direct `dspy.LM` instantiation is allowed inside `match/clarify.py`.
- **AC-B08** WHEN the LLM is called, `temperature` SHALL be `0` (determinism rule, AD-001).

**Validation failure + fallback**

- **AC-B09** WHEN the LLM response fails Pydantic validation on the first attempt, the system SHALL make exactly one retry, appending the validator error message to the DSPy input.
- **AC-B10** WHEN the LLM response fails Pydantic validation on the retry, the system SHALL activate the deterministic fallback parser (split on `";"`, regex for proficiency markers) and set `clarification_notes` to a string containing `"fallback=true"` to signal degraded mode without modifying the frozen model.
- **AC-B11** WHEN the fallback parser is active, the returned scorecard SHALL still be a valid `TargetProfileScorecard` (no exceptions propagate to the caller).

**Determinism**

- **AC-B12** WHEN `clarify_role` is called twice with identical inputs, the outputs SHALL be identical (same model, `temperature=0`, same DSPy version — no randomness).

**Hard-skill adjacency guard (AD-033)**

- **AC-B13** WHEN a skill is placed in `hard_depth_skills`, the scorecard SHALL NOT also list it in `desired_skills`.

## Out of scope
- Score, rank, or gate logic.
- Real Presidio NER (Lane C's task — build against the stub interface).
- Domains inference beyond what can be derived from the role text at hand.
- Any modification to `dsm/models.py`.
