"""Tests for the outbound leak-scan hard gate (a-003 T-003; AD-069). PII-5/6, LN-4."""

from __future__ import annotations

import pytest

from dsm.pii.leakscan import PIILeakError, assert_no_leak, leak_scan


def test_clean_text_passes() -> None:
    """PII-6: text with no residual known PII scans clean and the gate does not raise."""
    result = leak_scan("[[PII_0]] works in Kotlin", known_pii=["Aarav Sharma"])
    assert result.clean is True
    assert result.hits == []
    assert_no_leak("[[PII_0]] works in Kotlin", known_pii=["Aarav Sharma"])  # no raise


def test_residual_pii_blocks_and_fails() -> None:
    """PII-5/LN-4: a surviving known-PII string makes the gate raise (fails the build/eval)."""
    leaky = "Aarav Sharma works in Kotlin"
    result = leak_scan(leaky, known_pii=["Aarav Sharma"])
    assert result.clean is False
    assert "Aarav Sharma" in result.hits
    with pytest.raises(PIILeakError):
        assert_no_leak(leaky, known_pii=["Aarav Sharma"])


def test_scan_is_case_insensitive() -> None:
    result = leak_scan("contact AARAV.SHARMA@EE.COM", known_pii=["aarav.sharma@ee.com"])
    assert result.clean is False


def test_error_message_reports_count_not_value() -> None:
    """The raised message must not re-emit the PII value — only a count (tech.md)."""
    with pytest.raises(PIILeakError) as exc:
        assert_no_leak("Aarav Sharma", known_pii=["Aarav Sharma"])
    assert "Aarav Sharma" not in str(exc.value)
    assert "1 known-PII" in str(exc.value)


def test_blank_known_pii_ignored() -> None:
    result = leak_scan("anything", known_pii=["", "   "])
    assert result.clean is True
