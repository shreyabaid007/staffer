"""Query-time as-of freshness guard (B-1; AD-087; ee-query-architecture §6.3).

Compares the demand snapshot's ``demand_as_of`` against the supply snapshot's ``valid_as_of``
and returns a verdict the orchestrator acts on: ``ok`` (proceed), ``warn`` (proceed, flag every
assessment), or ``refuse`` (block the run — availability arithmetic is dishonest over a stale
snapshot). Pure datetime arithmetic: no LLM, no config import (the caller supplies
``max_staleness_days`` from ``config.reconcile.max_staleness_days``, AD-087).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel

# Verdict action values (FR-2 / AD-087).
OK = "ok"
WARN = "warn"
REFUSE = "refuse"


class FreshnessVerdict(BaseModel, frozen=True):
    """The freshness decision for a demand/supply pair (AD-087).

    ``action`` is one of ``"ok" | "warn" | "refuse"``. ``staleness_days`` is
    ``(demand_as_of − valid_as_of).days`` (negative when supply is fresher than demand).
    ``message`` is a human-readable explanation for logs / the no-run path.
    """

    action: str
    staleness_days: int
    message: str


def check_freshness(
    demand_as_of: date,
    valid_as_of: date,
    start_date: date,
    max_staleness_days: int = 30,
) -> FreshnessVerdict:
    """Decide whether the supply snapshot is fresh enough to serve the demand (AD-087).

    Decision tree (checked in this order so the more-specific ``warn`` wins over ``ok``):

    1. ``staleness_days > max_staleness_days`` → ``refuse`` (block the run).
    2. ``0 < staleness_days ≤ max_staleness_days`` **and** ``start_date < valid_as_of`` →
       ``warn`` (the role start already preceded the snapshot — backfilling an overdue role;
       flag every assessment).
    3. otherwise (including supply fresher than demand, ``staleness_days ≤ 0``) → ``ok``.

    Args:
        demand_as_of: the as-of date parsed from the Open Roles CSV banner.
        valid_as_of: the as-of date of the ingested supply snapshot (AD-070).
        start_date: the role's start date (only consulted on the ``warn`` branch).
        max_staleness_days: staleness tolerance; the caller passes
            ``config.reconcile.max_staleness_days`` (default 30).

    Returns:
        A frozen ``FreshnessVerdict`` with ``action``, ``staleness_days``, and a ``message``.
    """
    staleness_days = (demand_as_of - valid_as_of).days

    if staleness_days > max_staleness_days:
        return FreshnessVerdict(
            action=REFUSE,
            staleness_days=staleness_days,
            message=(
                f"Supply snapshot is {staleness_days}d stale "
                f"(> {max_staleness_days}d max): demand as of {demand_as_of}, "
                f"supply valid as of {valid_as_of}. Refusing — re-ingest fresh supply."
            ),
        )

    if staleness_days > 0 and start_date < valid_as_of:
        return FreshnessVerdict(
            action=WARN,
            staleness_days=staleness_days,
            message=(
                f"Role start {start_date} precedes the supply snapshot {valid_as_of} "
                f"({staleness_days}d stale, within {max_staleness_days}d): backfilling an "
                f"overdue role — every assessment is flagged."
            ),
        )

    return FreshnessVerdict(
        action=OK,
        staleness_days=staleness_days,
        message=(
            f"Supply is fresh enough ({staleness_days}d staleness "
            f"≤ {max_staleness_days}d max): demand as of {demand_as_of}, "
            f"supply valid as of {valid_as_of}."
        ),
    )
