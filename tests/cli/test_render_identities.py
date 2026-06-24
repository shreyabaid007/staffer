"""Unit tests for ``render_identities`` — the AD-107 output de-anonymisation step.

The query pipeline emits pseudonymised results (every identity field carries the ``candidate_id``,
AD-091); ``render_identities`` substitutes the real ``(name, email)`` from the vault at the CLI
edge. These tests fix the four acceptance criteria (AC-1…AC-4) plus purity/idempotence, with a
seeded ``InMemoryVault`` (no network, no file).
"""

from __future__ import annotations

from structlog.testing import capture_logs

from dsm.cli.commands import render_identities
from dsm.models import (
    Candidate,
    CandidateAssessment,
    CandidateSource,
    Exclusion,
    ExclusionLog,
    ExclusionReason,
    FeedbackSignals,
    FreeNow,
    Location,
    NearMiss,
    NoMatchResult,
    ProficiencyLevel,
    ShortlistResult,
    Skill,
)
from dsm.pii.vault import InMemoryVault

CID_A = "cid:aaa"
CID_B = "cid:bbb"
CID_C = "cid:ccc"


def _vault() -> InMemoryVault:
    """A vault seeded for A and B but **not** C (C exercises the miss path)."""
    vault = InMemoryVault()
    vault.put_identity(CID_A, "Ada Lovelace", "ada@example.com")
    vault.put_identity(CID_B, "Bo Diaz", "bo@example.com")
    return vault


def _candidate(cid: str) -> Candidate:
    """A serving candidate carrying the pseudonymised ``candidate_id`` as email+name (AD-091)."""
    return Candidate(
        email=cid,
        name=cid,
        location=Location(city="bengaluru"),
        availability=FreeNow(),
        skills=[Skill(name="kotlin", proficiency=ProficiencyLevel.ADVANCED)],
        feedback=FeedbackSignals(entries=[]),
        source=CandidateSource.BEACH,
    )


def _assessment(cid: str) -> CandidateAssessment:
    return CandidateAssessment(
        candidate=_candidate(cid),
        skill_match_score=0.9,
        feedback_score=0.8,
        combined_score=0.87,
        flags=[],
        evidence=[],
        narrative="Strong match.",
        hard_skill_coverage=1.0,
        desired_skill_coverage=0.5,
    )


def _near_miss(cid: str) -> NearMiss:
    return NearMiss(candidate_email=cid, name=cid, reason="availability", gap_summary="free 1wk late")


def _exclusion(cid: str) -> Exclusion:
    return Exclusion(candidate_email=cid, reason=ExclusionReason.LOCATION_MISMATCH, detail="wrong city")


def test_shortlist_identity_rendered() -> None:
    """AC-1: a ranked candidate's email+name become the vault's real values."""
    result = ShortlistResult(
        role_id="ROLE-01",
        ranked_assessments=[_assessment(CID_A)],
        total_eligible=1,
        exclusion_log=ExclusionLog(exclusions=[]),
        config_snapshot={},
    )
    rendered = render_identities(result, _vault())
    assert isinstance(rendered, ShortlistResult)
    cand = rendered.ranked_assessments[0].candidate
    assert cand.email == "ada@example.com"
    assert cand.name == "Ada Lovelace"


def test_no_match_near_misses_and_closest_rendered() -> None:
    """AC-2: every NearMiss in both near_misses and closest_on_skills is de-anonymised."""
    result = NoMatchResult(
        role_id="ROLE-05",
        reason="no eligible candidates",
        near_misses=[_near_miss(CID_A)],
        closest_on_skills=[_near_miss(CID_B)],
        exclusion_log=ExclusionLog(exclusions=[]),
    )
    rendered = render_identities(result, _vault())
    assert isinstance(rendered, NoMatchResult)
    assert rendered.near_misses[0].candidate_email == "ada@example.com"
    assert rendered.near_misses[0].name == "Ada Lovelace"
    assert rendered.closest_on_skills[0].candidate_email == "bo@example.com"
    assert rendered.closest_on_skills[0].name == "Bo Diaz"


def test_exclusion_log_rendered() -> None:
    """AC-3: every Exclusion.candidate_email is de-anonymised (shortlist + no-match)."""
    log = ExclusionLog(exclusions=[_exclusion(CID_A), _exclusion(CID_B)])
    result = ShortlistResult(
        role_id="ROLE-01",
        ranked_assessments=[_assessment(CID_A)],
        total_eligible=1,
        exclusion_log=log,
        config_snapshot={},
    )
    rendered = render_identities(result, _vault())
    emails = {e.candidate_email for e in rendered.exclusion_log.exclusions}
    assert emails == {"ada@example.com", "bo@example.com"}


def test_vault_miss_keeps_candidate_id_and_warns() -> None:
    """AC-4: an unknown candidate_id is kept verbatim + a PII-safe warning is logged, no crash.

    ``structlog.testing.capture_logs`` is used (not capsys/capfd) because the CLI's ``PrintLogger``
    binds ``sys.stderr`` at import time, so the fd/object swap fixtures miss it; capturing at the
    structlog event level is robust to the configured logger factory.
    """
    result = ShortlistResult(
        role_id="ROLE-01",
        ranked_assessments=[_assessment(CID_C)],  # C not in the vault
        total_eligible=1,
        exclusion_log=ExclusionLog(exclusions=[_exclusion(CID_C)]),
        config_snapshot={},
    )
    with capture_logs() as logs:
        rendered = render_identities(result, _vault())
    assert rendered.ranked_assessments[0].candidate.email == CID_C
    assert rendered.ranked_assessments[0].candidate.name == CID_C
    assert rendered.exclusion_log.exclusions[0].candidate_email == CID_C
    misses = [e for e in logs if e["event"] == "render.vault_miss_identity"]
    assert misses, "expected a vault-miss warning"
    # PII-safe: only the candidate_id is logged, and it warns once per distinct id (cached)
    assert all(e["candidate_id"] == CID_C for e in misses)
    assert len(misses) == 1


def test_render_does_not_mutate_input() -> None:
    """AC-6/AC-7: render returns copies — the frozen pipeline result stays pseudonymised."""
    original = ShortlistResult(
        role_id="ROLE-01",
        ranked_assessments=[_assessment(CID_A)],
        total_eligible=1,
        exclusion_log=ExclusionLog(exclusions=[_exclusion(CID_B)]),
        config_snapshot={},
    )
    rendered = render_identities(original, _vault())
    # input untouched (still pseudonymised) — render returns copies
    assert original.ranked_assessments[0].candidate.email == CID_A
    assert original.exclusion_log.exclusions[0].candidate_email == CID_B
    # output carries the real values
    assert rendered.ranked_assessments[0].candidate.email == "ada@example.com"
    assert rendered.exclusion_log.exclusions[0].candidate_email == "bo@example.com"
