"""Query-time text builders for the index layer (AD-091 split).

Write-time builders (``build_embed_text``, ``build_skill_set``, ``included_skills``) moved to
``dsm/index/build.py`` (the build edge). This module holds only **query-time** builders that
have no ``dsm.ingest`` dependency.
"""

from __future__ import annotations

from dsm.models import TargetProfileScorecard


def build_role_query_passage(scorecard: TargetProfileScorecard) -> str:
    """Build the role-side query passage for rerank (symmetric to candidate passage).

    Composition: skills (hard + desired names) + min_proficiency-derived seniority +
    clarification_notes. Capability-only, PII-free.

    Args:
        scorecard: the clarified role requirements.

    Returns:
        A deterministic, PII-free query passage string.
    """
    parts: list[str] = []

    hard_phrases: list[str] = []
    for s in sorted(scorecard.hard_depth_skills, key=lambda s: s.name):
        if s.min_proficiency is not None:
            hard_phrases.append(f"{s.name} {s.min_proficiency.value}")
        else:
            hard_phrases.append(s.name)
    if hard_phrases:
        parts.append("Required: " + ", ".join(hard_phrases) + ".")

    desired_names = sorted(s.name for s in scorecard.desired_skills)
    if desired_names:
        parts.append("Desired: " + ", ".join(desired_names) + ".")

    if scorecard.clarification_notes:
        parts.append(scorecard.clarification_notes)

    return " ".join(parts)
