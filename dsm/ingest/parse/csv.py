"""Step 2 — Parse (CSV): banner/as-of, per-sheet headers, quoting, verbatim rows.

Deterministic and LLM-free. Values are emitted verbatim as strings — zero normalization.
Malformed rows are logged, skipped, and counted; they never abort the file (C-INVALID-1).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

from dsm.ingest.lineage import log_invalid
from dsm.ingest.models import BronzeRecord, SourceType

_BANNER_RE = re.compile(r"^\s*as of\s+(.+?)\s*$", re.IGNORECASE)
_DATE_FORMATS = ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%d/%m/%Y", "%d-%m-%Y")


def _banner_value(first_line: str) -> str | None:
    """Return the date string from an ``as of <date>`` banner line, else ``None``."""
    m = _BANNER_RE.match(first_line)
    return m.group(1) if m else None


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def read_banner_date(data: bytes) -> date | None:
    """Parse the snapshot date from a supply CSV's first-line ``as of <date>`` banner.

    Returns ``None`` when there is no banner or the date is unparseable (C-BANNER-1).
    """
    text = data.decode("utf-8-sig", errors="replace")
    first = text.splitlines()[0] if text else ""
    value = _banner_value(first)
    return _parse_date(value) if value is not None else None


def parse_csv(
    data: bytes,
    source_type: SourceType,
    source_hash: str,
    *,
    run_id: str,
) -> list[BronzeRecord]:
    """Parse a supply CSV blob into verbatim ``BronzeRecord`` rows."""
    text = data.decode("utf-8-sig", errors="replace")
    if not text.strip():
        return []

    # Strip the banner line (if any) before the CSV body so it is never a header/row.
    first_line = text.splitlines()[0]
    if _banner_value(first_line) is not None:
        newline = text.find("\n")
        body = text[newline + 1 :] if newline != -1 else ""
    else:
        body = text

    # io.StringIO preserves embedded newlines in quoted fields (C-QUOTE-1).
    rows = list(csv.reader(io.StringIO(body)))
    if not rows:
        return []

    header = rows[0]
    records: list[BronzeRecord] = []
    for row_index, row in enumerate(rows[1:]):
        if len(row) != len(header):
            log_invalid(
                run_id=run_id,
                reason="column_count_mismatch",
                payload=repr(row),
                source_uri=source_hash,
            )
            continue
        records.append(
            BronzeRecord(
                source_hash=source_hash,
                source_type=source_type,
                row_index=row_index,
                raw=dict(zip(header, row, strict=True)),
            )
        )
    return records
