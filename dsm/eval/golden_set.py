"""Typed loader for the hand-labelled golden set (c-004, AD-104).

The golden set binds seed roles to expected shortlists, relevant sets (for
Recall@K), and per-candidate faithfulness labels. Labels are drafted by
machine and require human sign-off before judge validation or metric reporting.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_DEFAULT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "golden_set.json"
)


class GoldenSetMeta(BaseModel):
    """Provenance metadata for the golden set."""

    labeller: str
    label_date: str
    review_status: str
    notes: str = ""


class NarrativeFixture(BaseModel):
    """Pre-recorded narrative + context for faithfulness judge validation."""

    narrative: str
    candidate_context: str
    role_context: str


class GoldenSetCase(BaseModel):
    """A single labelled case in the golden set."""

    case_id: str
    role_fixture: str
    description: str = ""
    expected_shortlist: list[str]
    expected_relevant_set: list[str]
    faithfulness_labels: dict[str, bool] = Field(default_factory=dict)
    narrative_fixtures: dict[str, NarrativeFixture] = Field(default_factory=dict)


class GoldenSet(BaseModel):
    """The full golden set: metadata + list of labelled cases."""

    meta: GoldenSetMeta = Field(alias="_meta")
    cases: list[GoldenSetCase]

    model_config = {"populate_by_name": True}

    @property
    def is_signed_off(self) -> bool:
        """Labels are trusted only after human review."""
        return self.meta.review_status == "signed_off"


def load_golden_set(path: Path | None = None) -> GoldenSet:
    """Load and validate the golden set from JSON.

    Args:
        path: Override path to the golden set file.  Defaults to
              ``tests/fixtures/golden_set.json``.

    Returns:
        A validated ``GoldenSet`` instance.

    Raises:
        FileNotFoundError: If the golden set file does not exist.
        pydantic.ValidationError: If the JSON is malformed or missing fields.
    """
    target = path or _DEFAULT_PATH
    if not target.exists():
        raise FileNotFoundError(f"Golden set not found: {target}")

    data = json.loads(target.read_text())
    gs = GoldenSet.model_validate(data)

    if not gs.is_signed_off:
        logger.warning(
            "Golden set review_status=%r — labels are draft; "
            "judge validation and metric reporting require signed-off labels.",
            gs.meta.review_status,
        )

    return gs
