"""Tests for identity tokenization + vault (a-002 T-001; AD-067/AD-068/AD-076).

c-003 (T-003) adds ``get_identity`` to the Vault protocol + a persistent ``FileVault`` (AD-102):
ingest writes, a later query process reads, to drive the query-time deterministic redact pass.
"""

import os
from pathlib import Path

import pytest

from dsm.pii.vault import (
    FileVault,
    InMemoryVault,
    candidate_id,
    normalize_email,
)


def test_candidate_id_is_deterministic_and_stable() -> None:
    """Same email → same id, every time (ID-1, ID-2)."""
    first = candidate_id("priya@acme.example")
    second = candidate_id("priya@acme.example")
    assert first == second
    assert first.startswith("cid:")


def test_candidate_id_is_case_and_whitespace_insensitive() -> None:
    """Stable across snapshots that differ only in casing/whitespace (ID-2)."""
    assert candidate_id("  Priya@Acme.Example ") == candidate_id("priya@acme.example")


def test_candidate_id_is_collision_safe_across_distinct_emails() -> None:
    """Two people who share a first name but differ by email get different ids (ID-3)."""
    aarav_one = candidate_id("aarav.k@acme.example")
    aarav_two = candidate_id("aarav.m@acme.example")
    assert aarav_one != aarav_two


def test_candidate_id_does_not_echo_the_email() -> None:
    """The token never contains the raw email (ID-5)."""
    cid = candidate_id("priya@acme.example")
    assert "priya" not in cid
    assert "@" not in cid


def test_blank_email_is_rejected() -> None:
    with pytest.raises(ValueError):
        candidate_id("   ")


def test_missing_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DSM_CANDIDATE_ID_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DSM_CANDIDATE_ID_KEY"):
        candidate_id("priya@acme.example")


def test_key_changes_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The id is keyed — a different key yields a different token (not a bare hash)."""
    monkeypatch.setenv("DSM_CANDIDATE_ID_KEY", "key-a")
    with_a = candidate_id("priya@acme.example")
    monkeypatch.setenv("DSM_CANDIDATE_ID_KEY", "key-b")
    with_b = candidate_id("priya@acme.example")
    assert with_a != with_b


def test_normalize_email() -> None:
    assert normalize_email("  Foo@Bar.COM ") == "foo@bar.com"


def test_in_memory_vault_returns_refs_not_pii() -> None:
    vault = InMemoryVault()
    cid = candidate_id("priya@acme.example")
    name_ref, email_ref = vault.put_identity(cid, "Priya", "priya@acme.example")
    assert name_ref == f"name:{cid}"
    assert email_ref == f"email:{cid}"
    assert "priya" not in name_ref.lower()


def test_in_memory_vault_get_identity_round_trips() -> None:
    vault = InMemoryVault()
    cid = candidate_id("priya@acme.example")
    vault.put_identity(cid, "Priya Nair", "priya@acme.example")
    assert vault.get_identity(cid) == ("Priya Nair", "priya@acme.example")
    assert vault.get_identity("cid:unknown") is None


# ── c-003 T-003: persistent FileVault (AD-102) ───────────────────────────────────────────────


def test_file_vault_put_then_get_round_trips(tmp_path: Path) -> None:
    """R-07: write then read returns the identity; the JSON file is created on first write."""
    path = tmp_path / "identity" / "vault.json"
    vault = FileVault(path)
    cid = candidate_id("aarav@acme.example")
    name_ref, email_ref = vault.put_identity(cid, "Aarav Sharma", "aarav@acme.example")
    assert (name_ref, email_ref) == (f"name:{cid}", f"email:{cid}")
    assert path.exists()
    assert vault.get_identity(cid) == ("Aarav Sharma", "aarav@acme.example")


def test_file_vault_persists_across_instances(tmp_path: Path) -> None:
    """R-08: identities written by one instance (≈ ingest) are read by a fresh one (≈ query)."""
    path = tmp_path / "vault.json"
    cid = candidate_id("aarav@acme.example")
    FileVault(path).put_identity(cid, "Aarav Sharma", "aarav@acme.example")

    # A brand-new instance over the same path (separate "process") sees the prior write.
    assert FileVault(path).get_identity(cid) == ("Aarav Sharma", "aarav@acme.example")


def test_file_vault_missing_id_returns_none(tmp_path: Path) -> None:
    """R-07: a missing id is a normal None (→ NER-only redaction), never a crash."""
    assert FileVault(tmp_path / "vault.json").get_identity("cid:nope") is None


def test_file_vault_unreadable_file_is_empty_not_fatal(tmp_path: Path) -> None:
    """R-07: a corrupt store degrades to empty rather than crashing the query."""
    path = tmp_path / "vault.json"
    path.write_text("{ not json", encoding="utf-8")
    vault = FileVault(path)
    assert vault.get_identity("cid:any") is None
    # Still writable afterwards.
    cid = candidate_id("x@acme.example")
    vault.put_identity(cid, "X", "x@acme.example")
    assert FileVault(path).get_identity(cid) == ("X", "x@acme.example")


def test_file_vault_does_not_echo_pii_in_refs(tmp_path: Path) -> None:
    """The returned refs are pointers, never the raw identity (mirrors InMemoryVault)."""
    cid = candidate_id("priya@acme.example")
    name_ref, _ = FileVault(tmp_path / "v.json").put_identity(cid, "Priya", "priya@acme.example")
    assert "priya" not in name_ref.lower()


# ── c-003 review hardening (adversarial review fixes) ────────────────────────────────────────


def test_file_vault_drops_malformed_entries_without_coercion(tmp_path: Path) -> None:
    """Review[3]: a non-[str,str] entry is dropped, never str()-coerced into a junk identifier."""
    import json

    path = tmp_path / "vault.json"
    path.write_text(
        json.dumps(
            {
                "cid:good": ["Priya Nair", "priya@acme.example"],
                "cid:nested": [["Priya"], "priya@acme.example"],  # malformed
                "cid:short": ["only-one"],  # malformed
            }
        ),
        encoding="utf-8",
    )
    vault = FileVault(path)
    assert vault.get_identity("cid:good") == ("Priya Nair", "priya@acme.example")
    assert vault.get_identity("cid:nested") is None  # not coerced to "['Priya']"
    assert vault.get_identity("cid:short") is None


def test_file_vault_flush_is_atomic_no_temp_left(tmp_path: Path) -> None:
    """Review[4]: a successful flush leaves no .tmp artifact and a readable store."""
    path = tmp_path / "vault.json"
    vault = FileVault(path)
    cid = candidate_id("aarav@acme.example")
    vault.put_identity(cid, "Aarav Sharma", "aarav@acme.example")
    assert not (tmp_path / "vault.json.tmp").exists()
    assert FileVault(path).get_identity(cid) == ("Aarav Sharma", "aarav@acme.example")


# Sanity: the suite-wide fixed key is present (set in tests/conftest.py).
def test_suite_key_is_set() -> None:
    assert os.environ.get("DSM_CANDIDATE_ID_KEY")
