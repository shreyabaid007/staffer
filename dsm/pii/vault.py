"""Identity tokenization + vault (AD-067, AD-068; placement per AD-076).

``candidate_id = HMAC(email)`` is the stable internal key used everywhere downstream
(AD-067). Email is the identity/join input but is **never** persisted into derived
records — silver derives the ``candidate_id`` and drops the raw email. The encrypted
at-rest identity store (name/email keyed by ``candidate_id``, AD-068) is owned by Lane C
and hardened in a later slice; this module seeds the derivation, the ``Vault`` protocol,
and a minimal in-memory store so the contract is fixed (AD-076).

``dsm.ingest`` may import **only** this module from ``dsm.pii`` (NF-IMPORT-1, narrowed).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Protocol

_KEY_ENV = "DSM_CANDIDATE_ID_KEY"
_CID_PREFIX = "cid:"


def _key() -> bytes:
    """Return the HMAC key from the environment, failing fast if unset.

    No silent default: a missing key would make ``candidate_id`` non-reproducible across
    machines and trivially reversible, so we refuse rather than guess.

    Raises:
        RuntimeError: if ``DSM_CANDIDATE_ID_KEY`` is unset or empty.
    """
    raw = os.environ.get(_KEY_ENV, "")
    if not raw:
        raise RuntimeError(
            f"{_KEY_ENV} is not set — required to derive a stable candidate_id (AD-067)."
        )
    return raw.encode("utf-8")


def normalize_email(email: str) -> str:
    """Canonicalise an email for hashing: trimmed + lowercased.

    Keeps ``candidate_id`` stable across snapshots that differ only in casing/whitespace.
    """
    return email.strip().lower()


def candidate_id(email: str) -> str:
    """Derive the stable internal ``candidate_id`` for an email (AD-067).

    ``"cid:" + HMAC-SHA256(key, normalized_email)``. Deterministic for a fixed key, so the
    same email always yields the same id (stability) and different emails effectively never
    collide (collision-safety). The raw email is the input only — it is never returned.

    Args:
        email: the raw email from the supply row / resume / feedback.

    Returns:
        The ``"cid:<hex>"`` token.

    Raises:
        RuntimeError: if the HMAC key env var is unset.
        ValueError: if ``email`` is blank.
    """
    normalized = normalize_email(email)
    if not normalized:
        raise ValueError("cannot derive candidate_id from a blank email")
    digest = hmac.new(_key(), normalized.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{_CID_PREFIX}{digest}"


class Vault(Protocol):
    """Identity store keyed by ``candidate_id`` (AD-068/AD-098).

    Maps a ``candidate_id`` to opaque references for the consultant's name and email, and back
    again. The fully hardened implementation (encryption at rest, retention limits, purge-by-id)
    is Lane C's to land later; AD-098 adds the minimal **persistent** store + read path that the
    query-time deterministic redact pass needs (AD-097).
    """

    def put_identity(self, candidate_id: str, name: str, email: str) -> tuple[str, str]:
        """Store name + email for a ``candidate_id``; return ``(name_ref, email_ref)``."""
        ...

    def get_identity(self, candidate_id: str) -> tuple[str, str] | None:
        """Return ``(name, email)`` for a ``candidate_id``, or ``None`` if unknown (AD-098).

        The query-time PII boundary (AD-097) reads this to obtain the candidate's *known*
        identifiers for the deterministic redact-first pass + leak-scan. ``None`` (a missing id)
        is a normal, non-fatal outcome → an empty known-PII list → NER-only redaction.
        """
        ...


class InMemoryVault:
    """Minimal non-persistent ``Vault`` for tests and the seed contract.

    No encryption, no persistence — Lane C replaces this with the encrypted store. Refs are
    deterministic pointers (``name:<cid>`` / ``email:<cid>``) into the in-memory map.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, str]] = {}

    def put_identity(self, candidate_id: str, name: str, email: str) -> tuple[str, str]:
        self._store[candidate_id] = (name, email)
        return (f"name:{candidate_id}", f"email:{candidate_id}")

    def get_identity(self, candidate_id: str) -> tuple[str, str] | None:
        return self._store.get(candidate_id)


class FileVault:
    """Persistent, file-backed ``Vault`` keyed by ``candidate_id`` (AD-098).

    Ingest **writes** identities here (from the supply row name/email it already redacts with);
    a later, separate ``dsm match`` process **reads** them back to drive the query-time
    deterministic redact pass (AD-097). Backed by a single JSON file at ``path`` which **must be
    gitignored** (the project ignores ``data/identity/``).

    PLAINTEXT this slice — a deliberate, signed-off limitation (AD-098). **TODO(AD-068):** encrypt
    at rest, add retention limits, and support purge-by-``candidate_id``. This store only persists
    at-rest what already lived in-process during ingest; it does not touch the *outbound* guarantee
    (redact-first + leak-scan), and identities never reach a provider unredacted.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._store: dict[str, list[str]] = self._load()

    def _load(self) -> dict[str, list[str]]:
        """Read the store from disk; a missing/unreadable file is an empty store, never a crash."""
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return {str(k): [str(v[0]), str(v[1])] for k, v in raw.items() if len(v) == 2}

    def _flush(self) -> None:
        """Persist the full store (POC scale); creates the gitignored parent dir on first write."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._store, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )

    def put_identity(self, candidate_id: str, name: str, email: str) -> tuple[str, str]:
        """Upsert ``(name, email)`` for the id and flush; return ``(name_ref, email_ref)``."""
        self._store[candidate_id] = [name, email]
        self._flush()
        return (f"name:{candidate_id}", f"email:{candidate_id}")

    def get_identity(self, candidate_id: str) -> tuple[str, str] | None:
        value = self._store.get(candidate_id)
        if value is None:
            return None
        return (value[0], value[1])
