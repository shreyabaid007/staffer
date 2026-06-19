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
import os
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
    """Encrypted identity store keyed by ``candidate_id`` (AD-068).

    Maps a ``candidate_id`` to opaque references for the consultant's name and email. The
    real implementation (encryption at rest, retention limits, purge-by-id) is Lane C's to
    harden later; only the contract is fixed here (AD-076).
    """

    def put_identity(self, candidate_id: str, name: str, email: str) -> tuple[str, str]:
        """Store name + email for a ``candidate_id``; return ``(name_ref, email_ref)``."""
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
