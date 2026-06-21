"""Exact hard-skill filter (step 5) — ``EligiblePool`` → filtered pool + exclusions (B-1; §6.5).

A stated **hard** skill is matched **structurally**, never by cosine adjacency (AD-033/072): a
candidate clears iff every hard-skill *name* is in their ``skill_set`` **and**, for each hard
skill carrying a ``min_proficiency`` floor, their matching skill's proficiency is at or above it
(ordinal ``ProficiencyLevel`` comparison, ``≥`` inclusive). Adjacency (AD-035) is **never**
consulted here — it contributes only to desired-skill coverage downstream (B-2).

Deterministic and LLM-free. Excluded candidates are recorded as
``Exclusion(reason=HARD_SKILL_MISMATCH, …)`` (AD-088) so the no-match path can explain the gap.
"""

from __future__ import annotations

from dsm.models import (
    Candidate,
    EligiblePool,
    Exclusion,
    ExclusionReason,
    ProficiencyLevel,
    Skill,
    SkillRequirement,
)

# Ordinal ranking for the proficiency floor (StrEnum has no inherent ordering).
_PROFICIENCY_ORDER: tuple[ProficiencyLevel, ...] = (
    ProficiencyLevel.BEGINNER,
    ProficiencyLevel.INTERMEDIATE,
    ProficiencyLevel.ADVANCED,
    ProficiencyLevel.EXPERT,
)
_PROFICIENCY_RANK = {level: index for index, level in enumerate(_PROFICIENCY_ORDER)}


def _best_proficiency_by_name(skills: list[Skill]) -> dict[str, ProficiencyLevel]:
    """Map each skill name to the candidate's highest proficiency for it (handles duplicates)."""
    best: dict[str, ProficiencyLevel] = {}
    for skill in skills:
        current = best.get(skill.name)
        if current is None or _PROFICIENCY_RANK[skill.proficiency] > _PROFICIENCY_RANK[current]:
            best[skill.name] = skill.proficiency
    return best


def _hard_skill_gap(candidate: Candidate, hard_skills: list[SkillRequirement]) -> str | None:
    """Return a human-readable gap detail if the candidate misses a hard skill, else ``None``.

    A gap is either a hard skill whose *name* is absent from the candidate's skills, or a present
    skill whose proficiency is below the requirement's ``min_proficiency`` floor.
    """
    held = _best_proficiency_by_name(candidate.skills)
    missing = sorted(req.name for req in hard_skills if req.name not in held)
    below = sorted(
        f"{req.name} ({held[req.name].value} < {req.min_proficiency.value})"
        for req in hard_skills
        if req.min_proficiency is not None
        and req.name in held
        and _PROFICIENCY_RANK[held[req.name]] < _PROFICIENCY_RANK[req.min_proficiency]
    )
    if not missing and not below:
        return None

    parts: list[str] = []
    if missing:
        parts.append("missing hard skills: " + ", ".join(missing))
    if below:
        parts.append("below proficiency floor: " + ", ".join(below))
    return "; ".join(parts)


def exact_hard_skill_filter(
    pool: EligiblePool,
    hard_skills: list[SkillRequirement],
) -> tuple[EligiblePool, list[Exclusion]]:
    """Filter an eligible pool to candidates clearing every hard skill exactly (FR-4).

    Args:
        pool: the post-gate ``EligiblePool`` to filter (may be empty).
        hard_skills: the role's hard requirements (``SkillDepth.HARD``); an empty list passes
            everyone (no hard requirement to clear).

    Returns:
        ``(filtered_pool, exclusions)`` — ``filtered_pool`` keeps the input's ``scorecard_id``
        and only candidates that hold every hard skill at/above its floor; ``exclusions`` carries
        one ``HARD_SKILL_MISMATCH`` record per dropped candidate, with the gap in ``detail``.
        Order is preserved; no adjacency is consulted (AD-033/072).
    """
    survivors: list[Candidate] = []
    exclusions: list[Exclusion] = []
    for candidate in pool.candidates:
        gap = _hard_skill_gap(candidate, hard_skills)
        if gap is None:
            survivors.append(candidate)
        else:
            exclusions.append(
                Exclusion(
                    candidate_email=candidate.email,
                    reason=ExclusionReason.HARD_SKILL_MISMATCH,
                    detail=gap,
                )
            )
    return EligiblePool(candidates=survivors, scorecard_id=pool.scorecard_id), exclusions
