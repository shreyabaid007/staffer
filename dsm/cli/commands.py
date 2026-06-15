"""CLI command implementations."""

from typing import Any

import typer

from dsm.config import load_config
from dsm.index.stub import retrieve_candidates
from dsm.ingest.stub import get_stub_candidates, get_stub_role
from dsm.match.clarify import clarify_role
from dsm.match.gates import filter_candidates
from dsm.match.rank import rank_assessments
from dsm.match.score import score_candidate


def ranking_config() -> tuple[int, dict[str, Any]]:
    """Read ranking config from ``config/default.yaml`` (AD-043, AD-064).

    Rank is config-free, so the orchestrator owns this single read and the
    reproducibility snapshot it passes down.

    Returns:
        ``(top_k, config_snapshot)`` — the shortlist size and a snapshot of the weights,
        ``top_k``, and model IDs for reproducibility tracing.
    """
    config = load_config()
    top_k = int(config["ranking"]["top_k"])
    snapshot: dict[str, Any] = {
        "top_k": top_k,
        "weights": config["weights"],
        "models": config["models"],
    }
    return top_k, snapshot


def match(role_id: str = typer.Option("ROLE-STUB-01", "--role-id")) -> None:
    """Match candidates to a role (Slice 0 stub ingest, real gates + rank)."""
    role = get_stub_role()
    candidates = get_stub_candidates()

    scorecard = clarify_role(role)
    eligible_pool, exclusion_log = filter_candidates(candidates, scorecard)
    retrieved = retrieve_candidates(eligible_pool, scorecard, top_k=10)
    assessments = [score_candidate(c, scorecard) for c in retrieved]
    top_k, config_snapshot = ranking_config()
    result = rank_assessments(assessments, role.role_id, exclusion_log, top_k, config_snapshot)

    typer.echo(result.model_dump_json(indent=2))
