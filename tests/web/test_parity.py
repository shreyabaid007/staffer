"""NF-1 parity: the web ``/match/role`` path produces the same result as the CLI ``_match_role``.

Guards against the web layer forking the spine — both go through ``commands._run_role`` with the
same stubbed builders, so the ranked candidate ids + scores must match exactly.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import dsm.cli.commands as commands
from dsm.ingest.goldstore import write_gold
from dsm.models import ShortlistResult
from dsm.web import service
from tests.web.test_app import _CSV, _gold, _predict


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands, "_build_clarify_predictor", lambda config: None)
    monkeypatch.setattr(commands, "_build_score_predictor", lambda config: _predict)
    monkeypatch.setattr(commands, "_build_embed_client", lambda: None)
    monkeypatch.setattr(commands, "_build_query_store", lambda config, db_path="": None)
    monkeypatch.setattr(commands, "_build_near_miss_rationale_predictor", lambda config: None)


def test_web_match_role_matches_cli(wired: None, tmp_path: Path) -> None:
    gold_dir = tmp_path / "gold"
    write_gold(_gold("cid:a", valid_as_of=date(2026, 6, 10)), gold_dir)
    write_gold(_gold("cid:b", valid_as_of=date(2026, 6, 10)), gold_dir)
    csv_path = tmp_path / "open_roles.csv"
    csv_path.write_text(_CSV, encoding="utf-8")

    # CLI path (pseudonymised result, pre-render).
    cli_pseudo, _vault = commands._match_role("ROLE-Q1", csv_path, gold_dir, "", None)
    assert isinstance(cli_pseudo, ShortlistResult)
    cli_ids = [
        a.candidate.email for a in cli_pseudo.ranked_assessments
    ]  # pre-render = candidate_id
    cli_scores = [a.combined_score for a in cli_pseudo.ranked_assessments]

    # Web path.
    resp = service.match_role(_CSV.encode("utf-8"), "ROLE-Q1", gold_dir=gold_dir)
    web_ids = [a.candidate.candidate_id for a in resp.shortlist]
    web_scores = [a.combined_score for a in resp.shortlist]

    assert web_ids == cli_ids
    assert web_scores == cli_scores
