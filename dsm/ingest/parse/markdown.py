"""Step 2 — Parse (Markdown): email key + split into feedback items.

Deterministic and LLM-free: verbatim item text, no normalization. Feedback is keyed by the
email it contains (the join key, AD-012/AD-067 — tokenized to candidate_id only in silver).
A file with no extractable email key is logged, skipped whole, and counted (MD-INVALID-1).
"""

from __future__ import annotations

import logging
import re

from dsm.ingest.models import BronzeRecord, SourceType

_log = logging.getLogger(__name__)  # T-009 swaps invalid logging to lineage.log_invalid

# An explicit `email:` marker line wins; otherwise the first address anywhere in the doc.
_KEY_RE = re.compile(r"^\s*email:\s*(\S+@\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Split before each top-level `##` heading, keeping the heading with its item.
_ITEM_SPLIT_RE = re.compile(r"(?m)^(?=##\s)")


def _email_key(text: str) -> str:
    keyed = _KEY_RE.search(text)
    if keyed is not None:
        return keyed.group(1)
    first = _EMAIL_RE.search(text)
    return first.group(0) if first is not None else ""


def _split_items(text: str) -> list[str]:
    """Split into per-item markdown on top-level `##` headings.

    Content before the first heading (e.g. the `email:` line) is preamble, not an item. A
    document with no headings is treated as a single item (MD-SPLIT-1).
    """
    items = [p.strip() for p in _ITEM_SPLIT_RE.split(text) if p.strip().startswith("##")]
    if items:
        return items
    body = text.strip()
    return [body] if body else []


def _kind(item: str) -> str:
    """Derive item kind from its heading: `client` when the heading names a client review."""
    first_line = item.splitlines()[0].lower() if item else ""
    return "client" if "client" in first_line else "project"


def parse_markdown(data: bytes, source_hash: str, *, run_id: str) -> list[BronzeRecord]:
    """Parse a feedback Markdown blob into one verbatim ``BronzeRecord`` per item."""
    text = data.decode("utf-8-sig", errors="replace")
    email_key = _email_key(text)
    if not email_key:
        _log.warning(
            "invalid: feedback has no email key",
            extra={"reason": "no_email_key", "payload": source_hash, "run_id": run_id},
        )
        return []

    return [
        BronzeRecord(
            source_hash=source_hash,
            source_type=SourceType.FEEDBACK,
            row_index=row_index,
            raw={"email_key": email_key, "raw_markdown": item, "kind": _kind(item)},
        )
        for row_index, item in enumerate(_split_items(text))
    ]
