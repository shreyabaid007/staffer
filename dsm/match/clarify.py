"""Role clarification via DSPy — produces TargetProfileScorecard."""

from __future__ import annotations

import dspy
import yaml

from dsm.models import OpenRole, TargetProfileScorecard
from dsm.pii.pseudonymised_lm import PseudonymisedLM


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
        desc="JSON object {city, state|null, country, remote_eligible}"
    )
    clarification_notes: str = dspy.OutputField(
        desc="1-2 sentence reasoning; include 'fallback=true' if fallback parser was used"
    )


def _load_lm() -> PseudonymisedLM:
    with open("config/default.yaml") as f:
        cfg = yaml.safe_load(f)
    model = cfg["models"]["reasoning_llm"]
    return PseudonymisedLM(model=model, temperature=0)


dspy.configure(lm=_load_lm())


def clarify_role(role: OpenRole) -> TargetProfileScorecard:
    """Stub: echo role as scorecard. Real implementation lands in b-001 tasks B-003+."""
    from dsm.models import SkillDepth

    return TargetProfileScorecard(
        role_id=role.role_id,
        hard_depth_skills=[s for s in role.required_skills if s.depth == SkillDepth.HARD],
        desired_skills=[s for s in role.required_skills if s.depth == SkillDepth.DESIRED],
        location=role.location,
        co_location_required=role.co_location_required,
        start_date=role.start_date,
        availability_window_days=14,
    )
