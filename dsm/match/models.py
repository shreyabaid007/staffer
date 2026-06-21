"""Query-time intermediate contracts for the demand side (B-1; ee-query-architecture §6.1).

These are the typed outputs of demand-CSV parsing — the banner ``demand_as_of`` (needed by
the freshness guard, AD-087) plus the ordered ``OpenRole``s and a record of skipped rows. The
frozen domain types (``SkillDepth``, ``SkillRequirement``, ``OpenRole``) are **reused** from
``dsm.models`` — never redefined (one model per fact; ``docs/structure.md``).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from dsm.models import OpenRole


class OpenRolesBanner(BaseModel, frozen=True):
    """The parsed Open Roles CSV header banner (FR-1).

    ``demand_as_of`` is the as-of date the demand snapshot was authored; it drives the
    freshness guard against supply ``valid_as_of`` (AD-087). ``source_path`` records which
    file the batch came from, for lineage.
    """

    demand_as_of: date
    source_path: str


class DemandParseOutcome(BaseModel, frozen=True):
    """The full result of parsing one Open Roles CSV (FR-1).

    ``roles`` are ordered by ``Priority`` ascending; ``skipped`` holds one human-readable
    line per malformed row that was logged and dropped (never silently lost, NF-3-style).
    """

    banner: OpenRolesBanner
    roles: list[OpenRole]
    skipped: list[str] = Field(default_factory=list)
