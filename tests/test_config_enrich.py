"""Config + versioned-prompt loading for the enrich stage (a-003 T-005)."""

from __future__ import annotations

from dsm.config import load_config, load_prompt


def test_enrich_config_present() -> None:
    cfg = load_config()
    assert cfg["enrich"]["prompt_version"] == "enrich-v1"
    assert cfg["enrich"]["temperature"] == 0
    assert cfg["models"]["reasoning_llm"]  # model_version source of truth


def test_reconcile_staleness_present() -> None:
    cfg = load_config()
    assert isinstance(cfg["reconcile"]["max_staleness_days"], int)


def test_prompts_load_and_nonempty() -> None:
    profile = load_prompt("profile_extraction")
    feedback = load_prompt("feedback_extraction")
    assert "verbatim" in profile.lower() and len(profile) > 50
    assert "verbatim" in feedback.lower() and "sentiment" in feedback.lower()
