"""Tests for dsm/match/clarify.py — B-001: signature + LM wiring."""

import dspy

from dsm.match import clarify
from dsm.pii.pseudonymised_lm import PseudonymisedLM


def test_clarify_module_imports() -> None:
    assert hasattr(clarify, "ClarifyRole")
    assert hasattr(clarify, "clarify_role")


def test_clarify_role_signature_fields() -> None:
    sig = clarify.ClarifyRole
    assert "role_id" in sig.input_fields
    assert "role_title" in sig.input_fields
    assert "required_skills_raw" in sig.input_fields
    assert "description" in sig.input_fields
    assert "hard_depth_skills_json" in sig.output_fields
    assert "desired_skills_json" in sig.output_fields
    assert "location_json" in sig.output_fields
    assert "clarification_notes" in sig.output_fields


def test_configured_lm_is_pseudonymised() -> None:
    lm = dspy.settings.lm
    assert isinstance(lm, PseudonymisedLM), f"Expected PseudonymisedLM, got {type(lm).__name__}"
