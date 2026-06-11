"""Hardcoded stub data for end-to-end testing (Slice 0)."""

from datetime import date

from dsm.models import (
    Candidate,
    CandidateSource,
    FeedbackSignals,
    FreeNow,
    Location,
    NewJoiner,
    OpenRole,
    ProficiencyLevel,
    RollingOff,
    Skill,
    SkillDepth,
    SkillRequirement,
)


def get_stub_candidates() -> list[Candidate]:
    """Return 3 hardcoded candidates: Beach, RollingOff, NewJoiner."""
    return [
        Candidate(
            email="alice@example.com",
            name="Alice Patel",
            location=Location(city="London", country="UK"),
            availability=FreeNow(),
            skills=[
                Skill(name="python", proficiency=ProficiencyLevel.EXPERT),
                Skill(name="aws", proficiency=ProficiencyLevel.ADVANCED),
            ],
            feedback=FeedbackSignals(),
            source=CandidateSource.BEACH,
        ),
        Candidate(
            email="bob@example.com",
            name="Bob Singh",
            location=Location(city="Manchester", country="UK"),
            availability=RollingOff(expected_date=date(2026, 7, 1), confidence="high"),
            skills=[
                Skill(name="java", proficiency=ProficiencyLevel.ADVANCED),
                Skill(name="kubernetes", proficiency=ProficiencyLevel.INTERMEDIATE),
            ],
            feedback=FeedbackSignals(),
            source=CandidateSource.ROLLING_OFF,
        ),
        Candidate(
            email="carol@example.com",
            name="Carol Lee",
            location=Location(city="London", country="UK"),
            availability=NewJoiner(join_date=date(2026, 7, 15)),
            skills=[
                Skill(name="python", proficiency=ProficiencyLevel.INTERMEDIATE),
                Skill(name="react", proficiency=ProficiencyLevel.ADVANCED),
            ],
            feedback=FeedbackSignals(),
            source=CandidateSource.NEW_JOINER,
        ),
    ]


def get_stub_role() -> OpenRole:
    """Return a single hardcoded test role."""
    return OpenRole(
        role_id="ROLE-STUB-01",
        title="Senior Python Engineer",
        required_skills=[
            SkillRequirement(
                name="python", depth=SkillDepth.HARD, min_proficiency=ProficiencyLevel.INTERMEDIATE
            ),
            SkillRequirement(name="aws", depth=SkillDepth.DESIRED),
        ],
        location=Location(city="London", country="UK"),
        co_location_required=False,
        start_date=date(2026, 7, 1),
    )
