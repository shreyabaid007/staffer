"""Explain CLI tests — lineage dump structure (B-002 T-012; FR-7)."""

from __future__ import annotations

import json
import subprocess

from dsm.cli.commands import _build_lineage, run_match
from dsm.match.freshness import WARN, FreshnessVerdict
from dsm.models import (
    NoMatchResult,
    ShortlistResult,
)
from tests.cli.test_orchestrator import _candidate, _scorecard


class TestBuildLineage:
    """Test the lineage envelope structure."""

    def test_shortlist_lineage_structure(self) -> None:
        """ShortlistResult lineage has the required keys."""
        result = run_match([_candidate("a@x.com")], _scorecard())
        assert isinstance(result, ShortlistResult)
        lineage = _build_lineage(result)

        assert lineage["type"] == "ShortlistResult"
        assert lineage["recall_mode"] == "exhaustive"
        assert isinstance(lineage["exclusions"], list)
        assert isinstance(lineage["candidates"], list)
        assert "config_snapshot" in lineage
        assert lineage["freshness_verdict"] is None

    def test_per_candidate_breakdown(self) -> None:
        """Each candidate entry has sub-scores, flags, narrative, evidence."""
        result = run_match(
            [_candidate("a@x.com"), _candidate("b@x.com")],
            _scorecard(),
        )
        assert isinstance(result, ShortlistResult)
        lineage = _build_lineage(result)

        assert len(lineage["candidates"]) == 2
        for entry in lineage["candidates"]:
            assert "email" in entry
            assert "skill_match_score" in entry
            assert "feedback_score" in entry
            assert "combined_score" in entry
            assert "hard_skill_coverage" in entry
            assert "desired_skill_coverage" in entry
            assert "flags" in entry
            assert "narrative" in entry
            assert "evidence" in entry

    def test_no_match_lineage_structure(self) -> None:
        """NoMatchResult lineage has reason and near_misses."""
        result = run_match(
            [_candidate("wrong@x.com", skill="java")],
            _scorecard(hard_skill="kotlin"),
        )
        assert isinstance(result, NoMatchResult)
        lineage = _build_lineage(result)

        assert lineage["type"] == "NoMatchResult"
        assert "reason" in lineage
        assert "near_misses" in lineage
        assert lineage["recall_mode"] == "exhaustive"

    def test_exclusions_listed(self) -> None:
        """Gate exclusions appear in the lineage."""
        from datetime import date

        from dsm.models import RollingOff

        candidates = [
            _candidate("good@x.com"),
            _candidate(
                "late@x.com",
                availability=RollingOff(expected_date=date(2026, 12, 1), confidence="high"),
            ),
        ]
        result = run_match(candidates, _scorecard())
        lineage = _build_lineage(result)

        assert len(lineage["exclusions"]) >= 1

    def test_freshness_verdict_included(self) -> None:
        """Freshness verdict is included when present."""
        verdict = FreshnessVerdict(
            action=WARN,
            staleness_days=5,
            message="supply is 5d stale",
        )
        result = run_match([_candidate("a@x.com")], _scorecard(), freshness_verdict=verdict)
        lineage = _build_lineage(result, freshness_verdict=verdict)

        assert lineage["freshness_verdict"] is not None
        assert lineage["freshness_verdict"]["action"] == "warn"
        assert lineage["freshness_verdict"]["staleness_days"] == 5

    def test_recall_mode_hybrid(self) -> None:
        """Recall mode is 'hybrid' when set."""
        result = run_match([_candidate("a@x.com")], _scorecard())
        lineage = _build_lineage(result, recall_mode="hybrid")
        assert lineage["recall_mode"] == "hybrid"

    def test_config_snapshot_in_lineage(self) -> None:
        """Config snapshot is present in ShortlistResult lineage."""
        result = run_match([_candidate("a@x.com")], _scorecard())
        assert isinstance(result, ShortlistResult)
        lineage = _build_lineage(result)
        assert "top_k" in lineage["config_snapshot"]
        assert "weights" in lineage["config_snapshot"]


class TestExplainCLISmoke:
    """Smoke test the explain CLI command."""

    def test_explain_stub_path_outputs_valid_json(self) -> None:
        """dsm explain --role-id ROLE-STUB-01 outputs valid JSON lineage."""
        completed = subprocess.run(
            ["uv", "run", "dsm", "explain", "--role-id", "ROLE-STUB-01"],
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        payload = json.loads(completed.stdout)

        assert payload["type"] == "ShortlistResult"
        assert payload["recall_mode"] == "exhaustive"
        assert isinstance(payload["candidates"], list)
        assert len(payload["candidates"]) == 2
        assert "config_snapshot" in payload
