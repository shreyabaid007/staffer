"""Role clarification via DSPy — produces TargetProfileScorecard."""

from dsm.models import OpenRole, SkillDepth, TargetProfileScorecard


def clarify_role(role: OpenRole) -> TargetProfileScorecard:
    """Stub: echo role as scorecard."""
    return TargetProfileScorecard(
        role_id=role.role_id,
        hard_depth_skills=[s for s in role.required_skills if s.depth == SkillDepth.HARD],
        desired_skills=[s for s in role.required_skills if s.depth == SkillDepth.DESIRED],
        location=role.location,
        co_location_required=role.co_location_required,
        start_date=role.start_date,
        availability_window_days=14,
    )
