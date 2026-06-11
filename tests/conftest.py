"""Shared pytest fixtures."""

from datetime import date

import pytest

from dsm.models import (
    Candidate,
    CandidateSource,
    FeedbackSignals,
    FreeNow,
    Location,
    ProficiencyLevel,
    Skill,
    SkillDepth,
    SkillRequirement,
    TargetProfileScorecard,
)

_FUTURE_DATE = date(2026, 9, 1)


@pytest.fixture
def sample_candidates() -> list[Candidate]:
    return [
        Candidate(
            email="a@example.com",
            name="Alice",
            location=Location(city="London", country="UK"),
            availability=FreeNow(),
            skills=[Skill(name="python", proficiency=ProficiencyLevel.ADVANCED)],
            feedback=FeedbackSignals(),
            source=CandidateSource.BEACH,
        ),
        Candidate(
            email="b@example.com",
            name="Bob",
            location=Location(city="London", country="UK"),
            availability=FreeNow(),
            skills=[Skill(name="java", proficiency=ProficiencyLevel.INTERMEDIATE)],
            feedback=FeedbackSignals(),
            source=CandidateSource.BEACH,
        ),
    ]


@pytest.fixture
def sample_scorecard() -> TargetProfileScorecard:
    return TargetProfileScorecard(
        role_id="ROLE-TEST-01",
        hard_depth_skills=[SkillRequirement(name="python", depth=SkillDepth.HARD)],
        desired_skills=[],
        location=Location(city="London", country="UK"),
        co_location_required=False,
        start_date=_FUTURE_DATE,
    )
