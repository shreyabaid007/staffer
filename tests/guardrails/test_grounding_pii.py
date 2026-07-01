"""Boundary regression (c-009 review fix #1): grounding must send NO raw PII to the remote API.

``bespoke_minicheck`` is a remote validator. ``_grounded_narrative`` (composition root) must redact
the de-anonymised narrative + candidate sources under the vault identity and leak-scan before the
call, then de-anonymise the filtered result. This test captures exactly what the guard received and
asserts the candidate's real name/email never appear — while the round-tripped narrative is kept.
"""

from __future__ import annotations

from typing import Any

from dsm.cli.commands import _grounded_narrative
from dsm.models import (
    Candidate,
    CandidateSource,
    FeedbackEntry,
    FeedbackSignals,
    FeedbackSource,
    FreeNow,
    Location,
    ProficiencyLevel,
    Skill,
)
from dsm.pii.vault import InMemoryVault

_NAME = "Priya Nair"
_EMAIL = "priya@acme.example"
_CID = "cid:abc123"

# NER off so the test is hermetic + fast — the deterministic known-PII strip is the guarantee here.
_CONFIG = {"pii": {"ner_enabled": False}}


def _vault() -> InMemoryVault:
    vault = InMemoryVault()
    vault.put_identity(_CID, _NAME, _EMAIL)
    return vault


class _CapturingGuard:
    """Records the (already-redacted) text + context it is handed; echoes the narrative back."""

    def __init__(self) -> None:
        self.seen_narrative: str | None = None
        self.seen_context: str | None = None

    def validate(self, text: str, metadata: dict[str, Any] | None = None) -> Any:
        self.seen_narrative = text
        self.seen_context = (metadata or {}).get("context")
        return type("Outcome", (), {"validated_output": text, "validation_passed": True})()


def _candidate() -> Candidate:
    return Candidate(
        email=_CID,
        name=_CID,
        location=Location(city="Pune", country="India"),
        availability=FreeNow(),
        skills=[Skill(name="python", proficiency=ProficiencyLevel.ADVANCED)],
        feedback=FeedbackSignals(
            entries=[
                FeedbackEntry(source=FeedbackSource.CLIENT, text=f"{_NAME} led delivery well.")
            ]
        ),
        source=CandidateSource.BEACH,
        profile_summary=f"{_NAME} ({_EMAIL}) drove the payments platform.",
    )


def test_grounding_receives_no_raw_pii() -> None:
    guard = _CapturingGuard()
    narrative = f"{_NAME} is a strong engineer on payments."
    out = _grounded_narrative(guard, narrative, _candidate(), _vault(), _CONFIG)

    # The remote guard must have seen redacted text — no real name/email in narrative OR context.
    assert guard.seen_narrative is not None
    assert _NAME not in guard.seen_narrative
    assert _EMAIL not in guard.seen_narrative
    assert guard.seen_context is not None
    assert _NAME not in guard.seen_context
    assert _EMAIL not in guard.seen_context
    # …but the returned narrative is de-anonymised back to the original.
    assert out == narrative


def test_grounding_none_guard_is_passthrough() -> None:
    narrative = f"{_NAME} is a strong engineer."
    assert _grounded_narrative(None, narrative, _candidate(), _vault(), _CONFIG) == narrative
