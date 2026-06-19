"""Tests for identity tokenization + vault (a-002 T-001; AD-067/AD-068/AD-076)."""

import os

import pytest

from dsm.pii.vault import (
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


# Sanity: the suite-wide fixed key is present (set in tests/conftest.py).
def test_suite_key_is_set() -> None:
    assert os.environ.get("DSM_CANDIDATE_ID_KEY")
