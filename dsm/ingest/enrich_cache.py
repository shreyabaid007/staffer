"""Content-keyed enrich-extraction cache (c-011, AD-XXX; FR-5-AC-2).

Caches the *output* of the resume/feedback LLM extraction so a record whose content did not
change makes zero LLM calls on a later run. The key pins everything that can change the
extraction: the record's identity + source bytes and the pinned derivation versions — the
AD-066 pattern (`FileIntakeCache` precedent). A ``prompt_version`` / ``model_version`` bump
therefore re-extracts (the §11 rule, now enforced).

Storage is one JSON file per key under a gitignored ``data/enrich_cache/`` directory —
**derived data**: deleting the directory is always safe and merely re-runs the LLM. Writes
are atomic (temp + ``os.replace``) like the blob store; a corrupt or unreadable entry is a
miss; a write failure is logged and swallowed (a cache is best-effort, never load-bearing
for correctness). Failed/``None`` extractions are **not** cached, so a transient enrich
failure retries on the next run.

Wired at the composition root (``dsm/cli/commands.py::ingest``) — ``dsm.ingest.enrich``
itself stays cache-free.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from dsm.ingest.models import FeedbackExtraction, ProfileSummaryExtraction

_log = structlog.get_logger(__name__)


def enrich_cache_key(
    *,
    candidate_id: str,
    source_hash: str,
    raw_text: str | None,
    prompt_version: str,
    model_version: str,
) -> str:
    """Derive the cache key for one normalized record's extraction.

    ``candidate_id`` is in the key because ``known_pii`` (the redaction list) is
    per-candidate — the same bytes enriched under a different identity is a different call.
    ``raw_text`` (the exact enrichment input) distinguishes multiple feedback items parsed
    from one source blob and guards against a parser change altering the extracted text
    under an unchanged ``source_hash``.
    """
    text_hash = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()
    material = "\x1f".join(
        (candidate_id, source_hash, text_hash, prompt_version, model_version)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class _CacheEntry(BaseModel, frozen=True):
    """On-disk envelope: the extraction kind + its payload."""

    kind: Literal["resume", "feedback"]
    resume: ProfileSummaryExtraction | None = None
    feedback: FeedbackExtraction | None = None
    key_material: dict[str, str] = Field(default_factory=dict)  # debug provenance, not read back


class FileEnrichCache:
    """File-backed extraction cache: one JSON per key under ``cache_dir``."""

    def __init__(self, cache_dir: Path) -> None:
        self._dir = Path(cache_dir)

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def get_resume(self, key: str) -> ProfileSummaryExtraction | None:
        entry = self._read(key)
        return entry.resume if entry is not None and entry.kind == "resume" else None

    def get_feedback(self, key: str) -> FeedbackExtraction | None:
        entry = self._read(key)
        return entry.feedback if entry is not None and entry.kind == "feedback" else None

    def put_resume(
        self, key: str, value: ProfileSummaryExtraction, *, key_material: dict[str, str]
    ) -> None:
        self._write(key, _CacheEntry(kind="resume", resume=value, key_material=key_material))

    def put_feedback(
        self, key: str, value: FeedbackExtraction, *, key_material: dict[str, str]
    ) -> None:
        self._write(key, _CacheEntry(kind="feedback", feedback=value, key_material=key_material))

    def _read(self, key: str) -> _CacheEntry | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            return _CacheEntry.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _log.warning("enrich.cache_unreadable", key=key)
            return None

    def _write(self, key: str, entry: _CacheEntry) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._path(key).with_suffix(".tmp")
            tmp.write_text(entry.model_dump_json(), encoding="utf-8")
            os.replace(tmp, self._path(key))
        except OSError:
            _log.warning("enrich.cache_write_failed", key=key)
