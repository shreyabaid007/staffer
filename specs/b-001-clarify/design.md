# Design — Lane B · Clarify (b-001-clarify)

## Module touched
`dsm/match/clarify.py` — replaces the stub implementation. No other files modified (models frozen).

## DSPy Signature: `ClarifyRole`

```python
class ClarifyRole(dspy.Signature):
    """Parse a role description into a structured target profile.

    Rules:
    - Skills marked (expert) or (depth) → hard_depth_skills (SkillDepth.HARD)
    - Skills marked (nice to have) or (desired) → desired_skills (SkillDepth.DESIRED)
    - Unmarked required skills → hard_depth_skills by default
    - Composite location strings like "Bengaluru / remote-India" → remote_eligible=True
    - A hard skill MUST NOT appear in desired_skills (AD-033)
    """
    role_id: str = dspy.InputField()
    role_title: str = dspy.InputField()
    required_skills_raw: str = dspy.InputField(desc="raw required skills text")
    description: str = dspy.InputField(desc="free-text role description, may be empty")

    hard_depth_skills_json: str = dspy.OutputField(
        desc="JSON array of {name, depth, min_proficiency|null}"
    )
    desired_skills_json: str = dspy.OutputField(
        desc="JSON array of {name, depth, min_proficiency|null}"
    )
    location_json: str = dspy.OutputField(
        desc="JSON {city, state|null, country, remote_eligible}"
    )
    clarification_notes: str = dspy.OutputField(
        desc="1-2 sentence reasoning; include 'fallback=true' if fallback parser was used"
    )
```

DSPy program: `dspy.Predict(ClarifyRole)` — a single typed predict call, `temperature=0`.

## Parsing pipeline inside `clarify_role(role: OpenRole) → TargetProfileScorecard`

```
1. Serialise OpenRole to text fields (role_id, title, required_skills_raw, description).
2. Call dspy.Predict(ClarifyRole) via PseudonymisedLM.
3. Attempt Pydantic parse of output JSON fields → TargetProfileScorecard.
4. On ValidationError → retry once with error appended to description input.
5. On second ValidationError → activate deterministic fallback (see below).
6. Return TargetProfileScorecard.
```

## Deterministic fallback parser

Invoked only when both LLM attempts fail validation. Pure Python, no LLM:

1. Split `role.description` and serialised `required_skills_raw` on `";"` and `","`.
2. For each token, regex-match `(expert|depth)` → HARD; `(nice to have|desired)` → DESIRED; else → HARD.
3. Strip marker text; normalise skill name to lowercase.
4. Build minimal `TargetProfileScorecard` from `role` fields directly.
5. Set `clarification_notes` to include the literal string `"fallback=true"`.

## LM instantiation

```python
from dsm.pii.pseudonymised_lm import PseudonymisedLM

_lm = PseudonymisedLM(model=cfg.reasoning_model, temperature=0)
dspy.configure(lm=_lm)
```

`cfg.reasoning_model` comes from `config/default.yaml`. No hardcoded model strings.

## Skill serialisation helper

`_skills_to_raw(skills: list[SkillRequirement]) -> str` — joins as `"name (depth); ..."` so the LLM sees a consistent format regardless of how ingest stored them.

## Config keys used (read-only)
- `reasoning_model` — model ID string for OpenRouter via DSPy.
- `availability_window_days` — always `14`; scorecard hard-codes per AD-021 but reads from config.

## Edge cases
| Case | Behaviour |
|---|---|
| `description` is `None` | Pass empty string `""` to the LLM |
| `required_skills` is empty | LLM receives `""` for that field; fallback returns empty hard/desired lists |
| Composite location `"Bengaluru / remote-India"` | LLM must set `remote_eligible=True`; fallback regex on `"remote-india"` also sets it |
| Skill in both hard and desired | Post-parse invariant: remove from `desired_skills` if already in `hard_depth_skills` |
| LLM returns non-JSON | Caught as `ValidationError` (json.JSONDecodeError wrapped) → triggers retry/fallback |

## Eval cases to add

Seed roles (ROLE-01, ROLE-02 to start) as golden fixtures in `tests/match/fixtures/roles/`. Each fixture: `(OpenRole input, expected TargetProfileScorecard)`. New roles are added by dropping a fixture file — no test code changes needed. Run against mock LM in unit tests; run against live LM behind `DSM_LIVE_LM=1` env flag for Promptfoo suite.

Key golden invariants:
- ROLE-01: `hard_depth_skills` contains `{name: "kotlin", depth: HARD}`.
- ROLE-02: `location.remote_eligible` reflects the role's actual location constraint.
- Any role with `"(expert)"` marker: that skill is in `hard_depth_skills`, not `desired_skills`.
