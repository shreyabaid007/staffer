"""Importable seed fixtures for gates/rank tests (ROLE-01/02/03).

Each builder returns ``(list[Candidate], TargetProfileScorecard)`` — gates take the
scorecard, not the raw ``OpenRole``. These fixtures are the foundation for the C3 live
eval suite; keep them importable so ``dsm/eval/`` can reuse them without duplication.

See ``specs/c-001-gates-rank/design.md`` § "Test fixtures — design".
"""

from __future__ import annotations

from datetime import date

from dsm.models import (
    AvailabilityState,
    Candidate,
    CandidateSource,
    FeedbackSignals,
    FreeNow,
    Location,
    NewJoiner,
    ProficiencyLevel,
    RollingOff,
    Skill,
    SkillDepth,
    SkillRequirement,
    TargetProfileScorecard,
)


def _candidate(
    *,
    email: str,
    name: str,
    city: str,
    availability: AvailabilityState,
    source: CandidateSource,
    skill: str,
    remote_eligible: bool = False,
) -> Candidate:
    """Build a Candidate with one representative skill (gates ignore skills).

    Args:
        email: join key and tie-break key for ranking.
        name: display name surfaced in near-misses.
        city: India city for the location gate.
        availability: FreeNow / RollingOff / NewJoiner variant.
        source: supply-sheet tab the candidate comes from.
        skill: a single normalised lowercase skill name (proficiency=advanced).
        remote_eligible: whether the candidate will work from another location.

    Returns:
        A fully-typed ``Candidate`` suitable for the deterministic gates.
    """
    return Candidate(
        email=email,
        name=name,
        location=Location(city=city, remote_eligible=remote_eligible),
        availability=availability,
        skills=[Skill(name=skill, proficiency=ProficiencyLevel.ADVANCED)],
        feedback=FeedbackSignals(),
        source=source,
    )


def role_01() -> tuple[list[Candidate], TargetProfileScorecard]:
    """ROLE-01 — partial availability exclusion.

    Kotlin dev, Chennai, co-location, start 2026-07-01, window 14d → deadline 2026-07-15.
    Aarav (RollingOff 2026-08-01) is excluded on availability (+17d); the other four pass.
    """
    scorecard = TargetProfileScorecard(
        role_id="ROLE-01",
        hard_depth_skills=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)],
        desired_skills=[],
        location=Location(city="Chennai"),
        co_location_required=True,
        start_date=date(2026, 7, 1),
        availability_window_days=14,
    )
    candidates = [
        _candidate(
            email="aarav@example.com",
            name="Aarav",
            city="Chennai",
            availability=RollingOff(expected_date=date(2026, 8, 1), confidence="high"),
            source=CandidateSource.ROLLING_OFF,
            skill="kotlin",
        ),
        _candidate(
            email="karan@example.com",
            name="Karan",
            city="Chennai",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="kotlin",
        ),
        _candidate(
            email="vivaan@example.com",
            name="Vivaan",
            city="Chennai",
            availability=RollingOff(expected_date=date(2026, 7, 10), confidence="high"),
            source=CandidateSource.ROLLING_OFF,
            skill="kotlin",
        ),
        _candidate(
            email="rahul@example.com",
            name="Rahul",
            city="Chennai",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="kotlin",
        ),
        _candidate(
            email="vikram@example.com",
            name="Vikram",
            city="Chennai",
            availability=NewJoiner(join_date=date(2026, 7, 14)),
            source=CandidateSource.NEW_JOINER,
            skill="kotlin",
        ),
    ]
    return candidates, scorecard


def role_02() -> tuple[list[Candidate], TargetProfileScorecard]:
    """ROLE-02 — Chennai co-location filter (location gate isolation).

    React dev, Chennai, co-location, start 2026-07-01, window 14d. Own candidate set
    (no Aarav — he would fail availability and muddy the location test). Deepa and Nikhil
    are excluded on location; Karan, Rahul (city match) and Priya (remote_eligible) pass.
    """
    scorecard = TargetProfileScorecard(
        role_id="ROLE-02",
        hard_depth_skills=[SkillRequirement(name="react", depth=SkillDepth.HARD)],
        desired_skills=[],
        location=Location(city="Chennai"),
        co_location_required=True,
        start_date=date(2026, 7, 1),
        availability_window_days=14,
    )
    candidates = [
        _candidate(
            email="karan@example.com",
            name="Karan",
            city="Chennai",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="react",
        ),
        _candidate(
            email="rahul@example.com",
            name="Rahul",
            city="Chennai",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="react",
        ),
        _candidate(
            email="deepa@example.com",
            name="Deepa",
            city="Pune",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="react",
            remote_eligible=False,
        ),
        _candidate(
            email="nikhil@example.com",
            name="Nikhil",
            city="Bangalore",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="react",
            remote_eligible=False,
        ),
        _candidate(
            email="priya@example.com",
            name="Priya",
            city="Pune",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="react",
            remote_eligible=True,
        ),
    ]
    return candidates, scorecard


def role_03() -> tuple[list[Candidate], TargetProfileScorecard]:
    """ROLE-03 — total exclusion (empty pool, exercises both miss types).

    Java dev, Mumbai, co-location, start 2026-07-01, window 14d → deadline 2026-07-15.
    All four fail. Near-miss order per AD-063(b): Sanjay (avail +1d), Meera (avail +31d),
    Arjun (location, email-alphabetical before Kavita). Capped at 3 → [Sanjay, Meera, Arjun].
    """
    scorecard = TargetProfileScorecard(
        role_id="ROLE-03",
        hard_depth_skills=[SkillRequirement(name="java", depth=SkillDepth.HARD)],
        desired_skills=[],
        location=Location(city="Mumbai"),
        co_location_required=True,
        start_date=date(2026, 7, 1),
        availability_window_days=14,
    )
    candidates = [
        _candidate(
            email="sanjay@example.com",
            name="Sanjay",
            city="Mumbai",
            availability=RollingOff(expected_date=date(2026, 7, 16), confidence="high"),
            source=CandidateSource.ROLLING_OFF,
            skill="java",
        ),
        _candidate(
            email="meera@example.com",
            name="Meera",
            city="Mumbai",
            availability=NewJoiner(join_date=date(2026, 8, 15)),
            source=CandidateSource.NEW_JOINER,
            skill="java",
        ),
        _candidate(
            email="arjun@example.com",
            name="Arjun",
            city="Pune",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="java",
        ),
        _candidate(
            email="kavita@example.com",
            name="Kavita",
            city="Kolkata",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="java",
        ),
    ]
    return candidates, scorecard
