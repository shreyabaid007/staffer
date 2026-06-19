"""Reconcile — snapshot diff, tombstones, freshness guard (§5/§10; AD-070).

Supply CSVs are full snapshots, so each run is the current truth. Reconciliation diffs this run's
``candidate_id`` set against the **prior** set on disk and **tombstones** the departed (flag, not
delete — gold stays the audit record, RC-1/RC-4). Latest-snapshot-wins for supply state is enforced
in ``merge`` (``_latest_supply``); reconcile reports the departed set. The **freshness guard**
warns when the latest snapshot is stale; the refuse-vs-role-start half is a match-time call (RC-3).

Deterministic: the current set is this run's gold ids, the prior set is the on-disk listing, and
any ``today`` needed is **injected** — no wall clock (RC-5).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from dsm.ingest.goldstore import gold_hash
from dsm.ingest.models import GoldCandidate


class ReconcileResult(BaseModel):
    """Departed ids to tombstone + any snapshot-freshness warnings for the run."""

    tombstoned_ids: list[str] = Field(default_factory=list)
    freshness_warnings: list[str] = Field(default_factory=list)


def reconcile(current_ids: set[str], prior_ids: set[str]) -> ReconcileResult:
    """Diff current vs prior; ids present before but not now are tombstoned (RC-1, sorted)."""
    return ReconcileResult(tombstoned_ids=sorted(prior_ids - current_ids))


def freshness_guard(
    valid_as_of: date | None,
    *,
    max_staleness_days: int,
    today: date,
) -> list[str]:
    """Warn when the latest snapshot is older than the threshold (RC-3). ``today`` is injected."""
    if valid_as_of is None:
        return []
    age = (today - valid_as_of).days
    if age > max_staleness_days:
        return [
            f"snapshot is {age} days old (valid_as_of={valid_as_of}, "
            f"threshold={max_staleness_days}d) — verify before matching"
        ]
    return []


def tombstone(candidate: GoldCandidate) -> GoldCandidate:
    """Carry a departed entity forward with ``is_tombstoned=True`` and a refreshed ``gold_hash``.

    The flag is part of the content, so the hash changes — correct, because the entity changed and
    the index must re-process it (RC-4/GS-2).
    """
    flipped = candidate.model_copy(update={"is_tombstoned": True, "gold_hash": ""})
    return flipped.model_copy(update={"gold_hash": gold_hash(flipped)})
