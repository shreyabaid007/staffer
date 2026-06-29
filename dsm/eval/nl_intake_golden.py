"""Typed loader for the NL-intake parse-quality golden set (c-006; AD-XXX).

Binds real-style prose requests to (a) the signed-off "golden parse" (``recorded_intake`` — a
``RoleIntake``, replayed deterministically through ``assemble_role`` in the offline eval tier) and
(b) the expected assembled ``OpenRole`` / ``ClarificationNeeded``. The live tier runs the real LLM
on ``prose`` and checks it assembles to the same structural fields. Labels require human sign-off
(``review_status == "signed_off"``) before they are trusted — mirrors ``golden_set.py`` (AD-104).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from dsm.match.models import RoleIntake

logger = logging.getLogger(__name__)

_DEFAULT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "nl_intake_golden.json"
)


class NLIntakeMeta(BaseModel):
    """Provenance + the fixed run-date relative dates were labelled against."""

    labeller: str
    label_date: str
    review_status: str
    today: str  # ISO run-date the recorded relative dates resolve against (offline tier)
    notes: str = ""


class NLIntakeExpected(BaseModel):
    """The expected assembly outcome for a case.

    ``outcome="role"`` → an ``OpenRole`` with these structural fields; ``start_date`` (ISO) is the
    exact resolved date the *offline* tier asserts (the live tier only checks validity/window since
    the LLM resolves relative to the real today). ``outcome="clarification"`` → a
    ``ClarificationNeeded`` whose ``missing`` set is ``missing``.
    """

    outcome: Literal["role", "clarification"]
    location_city: str | None = None
    remote_within_country: bool = False
    co_location_required: bool | None = None
    hard_skills: list[str] = Field(default_factory=list)
    desired_skills: list[str] = Field(default_factory=list)
    exclude_cities: list[str] = Field(default_factory=list)  # c-007 query-side negation
    start_date: str | None = None  # ISO; offline-exact
    missing: list[str] = Field(default_factory=list)  # for the clarification outcome


class NLIntakeCase(BaseModel):
    """One golden phrasing: the prose, its golden parse, and the expected assembly."""

    case_id: str
    prose: str
    recorded_intake: RoleIntake  # the signed-off "golden parse" (reuses the real type)
    expected: NLIntakeExpected
    live: bool = False  # exercised by the key-gated live tier


class NLIntakeGolden(BaseModel):
    """The full NL-intake golden set: metadata + cases."""

    meta: NLIntakeMeta = Field(alias="_meta")
    cases: list[NLIntakeCase]

    model_config = {"populate_by_name": True}

    @property
    def is_signed_off(self) -> bool:
        """Labels are trusted only after human review."""
        return self.meta.review_status == "signed_off"


def load_nl_intake_golden(path: Path | None = None) -> NLIntakeGolden:
    """Load and validate the NL-intake golden set from JSON.

    Args:
        path: Override path; defaults to ``tests/fixtures/nl_intake_golden.json``.

    Returns:
        A validated ``NLIntakeGolden``.

    Raises:
        FileNotFoundError: if the file is missing.
        pydantic.ValidationError: if the JSON is malformed (e.g. a ``recorded_intake`` that is
            not a valid ``RoleIntake``).
    """
    target = path or _DEFAULT_PATH
    if not target.exists():
        raise FileNotFoundError(f"NL-intake golden set not found: {target}")
    golden = NLIntakeGolden.model_validate(json.loads(target.read_text(encoding="utf-8")))
    if not golden.is_signed_off:
        logger.warning(
            "NL-intake golden set review_status=%r — labels are draft, not trusted.",
            golden.meta.review_status,
        )
    return golden
