"""Importable seed fixtures for gates/rank/eval tests (ROLE-01/02/03).

Each builder returns ``(list[Candidate], TargetProfileScorecard)`` — gates take the
scorecard, not the raw ``OpenRole``. These fixtures are the foundation for the eval
suite; keep them importable so ``dsm/eval/`` can reuse them without duplication.

Enriched with ``profile_summary`` and ``FeedbackSignals`` (c-002, T-001) so that the
``evidence-cited`` invariant has real quotes to verify and the ``score`` step has substance.
"""

from __future__ import annotations

from datetime import date

from dsm.models import (
    AvailabilityState,
    Candidate,
    CandidateSource,
    FeedbackEntry,
    FeedbackSignals,
    FeedbackSource,
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
    remote_within_country: bool = False,
    onsite_cities: frozenset[str] = frozenset(),
    profile_summary: str | None = None,
    feedback_entries: list[FeedbackEntry] | None = None,
) -> Candidate:
    """Build a Candidate with one representative skill.

    Args:
        email: join key and tie-break key for ranking.
        name: display name surfaced in near-misses.
        city: India city for the location gate.
        availability: FreeNow / RollingOff / NewJoiner variant.
        source: supply-sheet tab the candidate comes from.
        skill: a single normalised lowercase skill name (proficiency=advanced).
        remote_within_country: works remote from a home base (AD-086; never clears onsite).
        onsite_cities: extra cities the candidate will work onsite in (AD-086).
        profile_summary: hand-authored background text for citation verification.
        feedback_entries: feedback items for the candidate.

    Returns:
        A fully-typed ``Candidate`` suitable for the deterministic gates and eval.
    """
    return Candidate(
        email=email,
        name=name,
        location=Location(
            city=city, remote_within_country=remote_within_country, onsite_cities=onsite_cities
        ),
        availability=availability,
        skills=[Skill(name=skill, proficiency=ProficiencyLevel.ADVANCED)],
        feedback=FeedbackSignals(entries=feedback_entries or []),
        source=source,
        profile_summary=profile_summary,
    )


def role_01() -> tuple[list[Candidate], TargetProfileScorecard]:
    """ROLE-01 — partial availability exclusion + hard-skill adjacency.

    Kotlin dev, Chennai, co-location, start 2026-07-01, window 14d → deadline 2026-07-15.
    Aarav (RollingOff 2026-08-01) is excluded on availability (+17d).
    Suresh (skill=java, not kotlin) is excluded by the exact hard-skill filter
    (HARD_SKILL_MISMATCH) — exercises the hard-skill-not-cleared-by-adjacency invariant.
    Desired skill ``java`` exercises the adjacency-flag invariant (Karan has ``kotlin``
    which is adjacent to ``java`` → adjacency credit → ADJACENCY_USED).
    The remaining four (Karan, Vivaan, Rahul, Vikram) pass all gates + filters.
    """
    scorecard = TargetProfileScorecard(
        role_id="ROLE-01",
        hard_depth_skills=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)],
        desired_skills=[SkillRequirement(name="java", depth=SkillDepth.DESIRED)],
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
            profile_summary="6 years Kotlin/JVM, senior developer.",
        ),
        _candidate(
            email="karan@example.com",
            name="Karan",
            city="Chennai",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="kotlin",
            profile_summary=(
                "5 years Kotlin/Android development, "
                "payments domain experience at fintech startup."
            ),
            feedback_entries=[
                FeedbackEntry(
                    source=FeedbackSource.INTERNAL_EE,
                    text="Strong Kotlin skills, delivered payment gateway integration on time.",
                    sentiment="positive",
                ),
            ],
        ),
        _candidate(
            email="vivaan@example.com",
            name="Vivaan",
            city="Chennai",
            availability=RollingOff(expected_date=date(2026, 7, 10), confidence="high"),
            source=CandidateSource.ROLLING_OFF,
            skill="kotlin",
            profile_summary="3 years Kotlin backend, microservices architecture.",
            feedback_entries=[
                FeedbackEntry(
                    source=FeedbackSource.CLIENT,
                    text="Good communicator, picked up Kotlin coroutines quickly.",
                    sentiment="positive",
                    retention_flag=True,
                ),
            ],
        ),
        _candidate(
            email="rahul@example.com",
            name="Rahul",
            city="Chennai",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="kotlin",
            profile_summary="4 years Kotlin/Spring Boot, banking sector.",
            feedback_entries=[
                FeedbackEntry(
                    source=FeedbackSource.INTERNAL_EE,
                    text="Reliable delivery, solid Kotlin fundamentals.",
                    sentiment="positive",
                ),
            ],
        ),
        _candidate(
            email="vikram@example.com",
            name="Vikram",
            city="Chennai",
            availability=NewJoiner(join_date=date(2026, 7, 14)),
            source=CandidateSource.NEW_JOINER,
            skill="kotlin",
            profile_summary="2 years Kotlin, recent bootcamp graduate.",
        ),
        _candidate(
            email="suresh@example.com",
            name="Suresh",
            city="Chennai",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="java",
            profile_summary="5 years Java/Spring, enterprise banking systems.",
            feedback_entries=[
                FeedbackEntry(
                    source=FeedbackSource.INTERNAL_EE,
                    text="Strong Java backend developer, reliable delivery.",
                    sentiment="positive",
                ),
            ],
        ),
    ]
    return candidates, scorecard


def role_02() -> tuple[list[Candidate], TargetProfileScorecard]:
    """ROLE-02 — Chennai co-location filter (location gate isolation).

    React dev, Chennai, co-location, start 2026-07-01, window 14d. Own candidate set
    (no Aarav — he would fail availability and muddy the location test). Deepa and Nikhil
    are excluded on location; Karan, Rahul (city match) and Priya (Chennai in onsite_cities,
    AD-086) pass.
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
            profile_summary="4 years React/TypeScript, component library development.",
            feedback_entries=[
                FeedbackEntry(
                    source=FeedbackSource.INTERNAL_EE,
                    text="Built reusable React component library.",
                    sentiment="positive",
                ),
            ],
        ),
        _candidate(
            email="rahul@example.com",
            name="Rahul",
            city="Chennai",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="react",
            profile_summary="3 years React/Next.js, e-commerce frontends.",
            feedback_entries=[
                FeedbackEntry(
                    source=FeedbackSource.CLIENT,
                    text="Delivered React migration ahead of schedule.",
                    sentiment="positive",
                ),
            ],
        ),
        _candidate(
            email="deepa@example.com",
            name="Deepa",
            city="Pune",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="react",
            profile_summary="3 years React, dashboard applications.",
        ),
        _candidate(
            email="nikhil@example.com",
            name="Nikhil",
            city="Bangalore",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="react",
            profile_summary="2 years React Native, mobile development.",
        ),
        _candidate(
            email="priya@example.com",
            name="Priya",
            city="Pune",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="react",
            onsite_cities=frozenset({"Chennai"}),
            profile_summary="5 years React/Vue.js, design system specialist.",
            feedback_entries=[
                FeedbackEntry(
                    source=FeedbackSource.INTERNAL_EE,
                    text="Excellent React architecture skills.",
                    sentiment="positive",
                ),
            ],
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
            profile_summary="6 years Java/Spring Boot, microservices.",
        ),
        _candidate(
            email="meera@example.com",
            name="Meera",
            city="Mumbai",
            availability=NewJoiner(join_date=date(2026, 8, 15)),
            source=CandidateSource.NEW_JOINER,
            skill="java",
            profile_summary="2 years Java, recent graduate.",
        ),
        _candidate(
            email="arjun@example.com",
            name="Arjun",
            city="Pune",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="java",
            profile_summary="4 years Java backend development.",
        ),
        _candidate(
            email="kavita@example.com",
            name="Kavita",
            city="Kolkata",
            availability=FreeNow(),
            source=CandidateSource.BEACH,
            skill="java",
            profile_summary="3 years Java enterprise applications.",
        ),
    ]
    return candidates, scorecard
