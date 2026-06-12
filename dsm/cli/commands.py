"""CLI command implementations."""

import typer

from dsm.index.stub import retrieve_candidates
from dsm.ingest.stub import get_stub_candidates, get_stub_role
from dsm.match.clarify import clarify_role
from dsm.match.gates import filter_candidates
from dsm.match.rank import rank_assessments
from dsm.match.score import score_candidate


def match(role_id: str = typer.Option("ROLE-STUB-01", "--role-id")) -> None:
    """Match candidates to a role (Slice 0: stubbed end-to-end)."""
    role = get_stub_role()
    candidates = get_stub_candidates()

    scorecard = clarify_role(role)
    eligible_pool, exclusion_log = filter_candidates(candidates, scorecard)
    retrieved = retrieve_candidates(eligible_pool, scorecard, top_k=10)
    assessments = [score_candidate(c, scorecard) for c in retrieved]
    result = rank_assessments(assessments, role.role_id, exclusion_log, top_k=5)

    typer.echo(result.model_dump_json(indent=2))
