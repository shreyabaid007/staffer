"""Contract instantiation tests for dsm/models.py (task F-004)."""

from datetime import date

import pytest
from pydantic import ValidationError

from dsm.models import (
    AvailabilityState,
    Candidate,
    CandidateAssessment,
    CandidateSource,
    EligiblePool,
    EvidenceCitation,
    EvidenceSource,
    Exclusion,
    ExclusionLog,
    ExclusionReason,
    FeedbackEntry,
    FeedbackSignals,
    FeedbackSource,
    Flag,
    FlagType,
    FreeNow,
    Location,
    NearMiss,
    NewJoiner,
    NoMatchResult,
    OpenRole,
    ProficiencyLevel,
    RollingOff,
    ShortlistResult,
    Skill,
    SkillDepth,
    SkillRequirement,
    TargetProfileScorecard,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FUTURE_DATE = date(2026, 9, 1)
ROLE_START = date(2026, 8, 1)


@pytest.fixture
def location() -> Location:
    return Location(city="Chennai", country="India")


@pytest.fixture
def skill() -> Skill:
    return Skill(name="python", proficiency=ProficiencyLevel.ADVANCED)


@pytest.fixture
def free_now() -> FreeNow:
    return FreeNow()


@pytest.fixture
def rolling_off() -> RollingOff:
    return RollingOff(expected_date=FUTURE_DATE, confidence="high")


@pytest.fixture
def new_joiner() -> NewJoiner:
    return NewJoiner(join_date=FUTURE_DATE)


@pytest.fixture
def feedback_entry() -> FeedbackEntry:
    return FeedbackEntry(source=FeedbackSource.INTERNAL_EE, text="Great communicator.")


@pytest.fixture
def feedback_signals(feedback_entry: FeedbackEntry) -> FeedbackSignals:
    return FeedbackSignals(entries=[feedback_entry])


@pytest.fixture
def candidate(location: Location, skill: Skill, feedback_signals: FeedbackSignals) -> Candidate:
    return Candidate(
        email="alice@ee.com",
        name="Alice",
        location=location,
        availability=FreeNow(),
        skills=[skill],
        feedback=feedback_signals,
        source=CandidateSource.BEACH,
    )


@pytest.fixture
def skill_req() -> SkillRequirement:
    return SkillRequirement(name="python", depth=SkillDepth.HARD)


@pytest.fixture
def open_role(location: Location, skill_req: SkillRequirement) -> OpenRole:
    return OpenRole(
        role_id="ROLE-001",
        title="Senior Engineer",
        required_skills=[skill_req],
        location=location,
        co_location_required=True,
        start_date=ROLE_START,
    )


@pytest.fixture
def scorecard(location: Location, skill_req: SkillRequirement) -> TargetProfileScorecard:
    return TargetProfileScorecard(
        role_id="ROLE-001",
        hard_depth_skills=[skill_req],
        desired_skills=[],
        location=location,
        co_location_required=True,
        start_date=ROLE_START,
    )


@pytest.fixture
def exclusion() -> Exclusion:
    return Exclusion(
        candidate_email="bob@ee.com",
        reason=ExclusionReason.LOCATION_MISMATCH,
        detail="Candidate is in Mumbai; role requires Chennai.",
    )


@pytest.fixture
def exclusion_log(exclusion: Exclusion) -> ExclusionLog:
    return ExclusionLog(exclusions=[exclusion])


@pytest.fixture
def eligible_pool(candidate: Candidate) -> EligiblePool:
    return EligiblePool(candidates=[candidate], scorecard_id="ROLE-001")


@pytest.fixture
def flag() -> Flag:
    return Flag(type=FlagType.ROLL_OFF_UNCERTAIN, message="Roll-off date may slip.")


@pytest.fixture
def evidence() -> EvidenceCitation:
    return EvidenceCitation(
        source=EvidenceSource.SUPPLY_SHEET,
        text="Available from 2026-09-01",
        metadata={"row": "42"},
    )


@pytest.fixture
def assessment(
    candidate: Candidate, flag: Flag, evidence: EvidenceCitation
) -> CandidateAssessment:
    return CandidateAssessment(
        candidate=candidate,
        skill_match_score=0.9,
        feedback_score=0.7,
        combined_score=0.7 * 0.9 + 0.3 * 0.7,
        flags=[flag],
        evidence=[evidence],
        narrative="Strong Python skills; roll-off date uncertain.",
        hard_skill_coverage=1.0,
        desired_skill_coverage=0.5,
    )


# ---------------------------------------------------------------------------
# Happy-path instantiation
# ---------------------------------------------------------------------------


def test_location_instantiates(location: Location) -> None:
    assert location.city == "Chennai"
    assert location.remote_within_country is False
    assert location.onsite_cities == frozenset()


def test_skill_instantiates(skill: Skill) -> None:
    assert skill.name == "python"
    assert skill.proficiency == ProficiencyLevel.ADVANCED


def test_free_now_instantiates(free_now: FreeNow) -> None:
    assert free_now.type == "free_now"


def test_rolling_off_instantiates(rolling_off: RollingOff) -> None:
    assert rolling_off.confidence == "high"


def test_new_joiner_instantiates(new_joiner: NewJoiner) -> None:
    assert new_joiner.join_date == FUTURE_DATE


def test_feedback_entry_instantiates(feedback_entry: FeedbackEntry) -> None:
    assert feedback_entry.retention_flag is False


def test_feedback_signals_instantiates(feedback_signals: FeedbackSignals) -> None:
    assert len(feedback_signals.entries) == 1


def test_candidate_instantiates(candidate: Candidate) -> None:
    assert candidate.email == "alice@ee.com"
    assert candidate.profile_summary is None


def test_open_role_instantiates(open_role: OpenRole) -> None:
    assert open_role.role_id == "ROLE-001"
    assert open_role.preferred_skills == []


def test_scorecard_instantiates(scorecard: TargetProfileScorecard) -> None:
    assert scorecard.availability_window_days == 14


def test_exclusion_log_instantiates(exclusion_log: ExclusionLog) -> None:
    assert len(exclusion_log.exclusions) == 1


def test_eligible_pool_instantiates(eligible_pool: EligiblePool) -> None:
    assert len(eligible_pool.candidates) == 1


def test_flag_instantiates(flag: Flag) -> None:
    assert flag.type == FlagType.ROLL_OFF_UNCERTAIN


def test_evidence_citation_instantiates(evidence: EvidenceCitation) -> None:
    assert evidence.metadata["row"] == "42"


def test_candidate_assessment_instantiates(assessment: CandidateAssessment) -> None:
    assert 0.0 <= assessment.combined_score <= 1.0


def test_shortlist_result_instantiates(
    assessment: CandidateAssessment, exclusion_log: ExclusionLog
) -> None:
    result = ShortlistResult(
        role_id="ROLE-001",
        ranked_assessments=[assessment],
        total_eligible=3,
        exclusion_log=exclusion_log,
        config_snapshot={"top_k": 5},
    )
    assert result.total_eligible == 3


def test_near_miss_instantiates() -> None:
    nm = NearMiss(
        candidate_email="carol@ee.com",
        name="Carol",
        reason="Availability mismatch",
        gap_summary="Free 3 weeks late",
    )
    assert nm.name == "Carol"


def test_no_match_result_instantiates(exclusion_log: ExclusionLog) -> None:
    result = NoMatchResult(
        role_id="ROLE-001",
        reason="No candidates passed location gate",
        near_misses=[],
        exclusion_log=exclusion_log,
    )
    assert result.near_misses == []


# ---------------------------------------------------------------------------
# Availability discriminated union
# ---------------------------------------------------------------------------


def test_availability_discriminator_free_now() -> None:
    from pydantic import TypeAdapter

    ta: TypeAdapter[AvailabilityState] = TypeAdapter(AvailabilityState)  # type: ignore[type-arg]
    obj = ta.validate_python({"type": "free_now"})
    assert isinstance(obj, FreeNow)


def test_availability_discriminator_rolling_off() -> None:
    from pydantic import TypeAdapter

    ta: TypeAdapter[AvailabilityState] = TypeAdapter(AvailabilityState)  # type: ignore[type-arg]
    obj = ta.validate_python(
        {"type": "rolling_off", "expected_date": "2026-09-01", "confidence": "low"}
    )
    assert isinstance(obj, RollingOff)
    assert obj.confidence == "low"


def test_availability_discriminator_new_joiner() -> None:
    from pydantic import TypeAdapter

    ta: TypeAdapter[AvailabilityState] = TypeAdapter(AvailabilityState)  # type: ignore[type-arg]
    obj = ta.validate_python({"type": "new_joiner", "join_date": "2026-09-01"})
    assert isinstance(obj, NewJoiner)


# ---------------------------------------------------------------------------
# Validation rejection — bad inputs
# ---------------------------------------------------------------------------


def test_candidate_rejects_missing_email() -> None:
    with pytest.raises(ValidationError):
        Candidate(  # type: ignore[call-arg]
            name="No Email",
            location=Location(city="Chennai"),
            availability=FreeNow(),
            skills=[],
            feedback=FeedbackSignals(),
            source=CandidateSource.BEACH,
        )


def test_open_role_rejects_bad_start_date() -> None:
    with pytest.raises(ValidationError):
        OpenRole(
            role_id="R",
            title="T",
            required_skills=[],
            location=Location(city="Chennai"),
            co_location_required=False,
            start_date="not-a-date",  # type: ignore[arg-type]
        )


def test_rolling_off_rejects_missing_date() -> None:
    with pytest.raises(ValidationError):
        RollingOff(confidence="high")  # type: ignore[call-arg]


def test_rolling_off_rejects_bad_confidence() -> None:
    with pytest.raises(ValidationError):
        RollingOff(expected_date=FUTURE_DATE, confidence="maybe")  # type: ignore[arg-type]


def test_skill_rejects_bad_proficiency() -> None:
    with pytest.raises(ValidationError):
        Skill(name="python", proficiency="wizard")  # type: ignore[arg-type]


def test_exclusion_rejects_bad_reason() -> None:
    with pytest.raises(ValidationError):
        Exclusion(candidate_email="x@ee.com", reason="vibes", detail="nope")  # type: ignore[arg-type]


def test_grade_shared_home_reexported_from_ingest() -> None:
    """AD-091: Grade lives in dsm.models, re-exported from dsm.ingest.models (same object)."""
    from dsm.ingest.models import Grade as IngestGrade
    from dsm.models import Grade

    assert IngestGrade is Grade
    assert Grade.LEAD_CONSULTANT.value == "lead_consultant"


def test_candidate_store_protocol_is_structural() -> None:
    """AD-091: a class with get(ids)->list[Candidate] satisfies the runtime-checkable port."""
    from dsm.models import CandidateStore

    class _FakeStore:
        def get(self, candidate_ids: list[str]) -> list[Candidate]:
            return []

    assert isinstance(_FakeStore(), CandidateStore)
    assert not isinstance(object(), CandidateStore)


def test_flagtype_has_freshness_warning() -> None:
    """AD-092: FRESHNESS_WARNING added so a warn freshness verdict surfaces as a per-line Flag."""
    from dsm.models import FlagType

    assert FlagType.FRESHNESS_WARNING.value == "freshness_warning"
