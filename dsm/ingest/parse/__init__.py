"""Step 2 — Parse: route a bronze blob to its parser by source type.

Each parser takes ``(bytes, source_hash)`` and emits ``list[BronzeRecord]`` (or logs+skips).
"""

from __future__ import annotations

from dsm.ingest.models import BronzeRecord, SourceType
from dsm.ingest.parse.csv import parse_csv
from dsm.ingest.parse.pdf import parse_pdf

_SUPPLY_TYPES = {
    SourceType.SUPPLY_BEACH,
    SourceType.SUPPLY_ROLLING_OFF,
    SourceType.SUPPLY_NEW_JOINERS,
}


def parse_blob(
    data: bytes,
    source_type: SourceType,
    source_hash: str,
    *,
    run_id: str,
) -> list[BronzeRecord]:
    """Dispatch a bronze blob to the parser for its ``source_type``."""
    if source_type in _SUPPLY_TYPES:
        return parse_csv(data, source_type, source_hash, run_id=run_id)
    if source_type is SourceType.RESUME:
        return parse_pdf(data, source_hash, run_id=run_id)
    raise NotImplementedError(f"no parser wired for {source_type}")  # FEEDBACK: T-008
