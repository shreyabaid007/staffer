"""Tests for the generic PII redactor (a-003 T-002; AD-069/AD-078). PII-1..4."""

from __future__ import annotations

from dsm.pii.redact import NerSpan, RedactionResult, deanonymize, redact

_RESUME = (
    "Aarav Sharma led the payments platform at Meridian Pay, "
    "building card-authorization services in Kotlin. Contact aarav.sharma@ee.com."
)
_KNOWN = ["Aarav Sharma", "aarav.sharma@ee.com"]


def test_known_name_and_email_removed() -> None:
    """PII-1: deterministic pass strips the known name + email, leaving placeholders."""
    result = redact(_RESUME, known_pii=_KNOWN, ner=lambda _t: [])
    assert "Aarav Sharma" not in result.text
    assert "aarav.sharma@ee.com" not in result.text
    assert "[[PII_0]]" in result.text  # at least one known-PII placeholder
    # The non-PII capability text survives untouched.
    assert "card-authorization services in Kotlin" in result.text


def test_case_insensitive_known_match() -> None:
    """PII-1: known identifiers are matched case-insensitively."""
    result = redact("aarav sharma and AARAV SHARMA", known_pii=["Aarav Sharma"], ner=lambda _t: [])
    assert "sharma" not in result.text.lower()


def test_ner_residual_tokenized_via_seam() -> None:
    """PII-2: a residual unknown name / client org from the NER seam is also tokenized."""

    def fake_ner(_text: str) -> list[NerSpan]:
        return [("Meridian Pay", "ORG"), ("Priya Nair", "PERSON")]

    text = "Reviewed by Priya Nair at Meridian Pay."
    result = redact(text, known_pii=[], ner=fake_ner)
    assert "Meridian Pay" not in result.text
    assert "Priya Nair" not in result.text
    assert "[[NER_0]]" in result.text


def test_deanonymize_round_trips() -> None:
    """PII-3: the in-memory mapping reconstructs the original — and is the only state."""
    result = redact(_RESUME, known_pii=_KNOWN, ner=lambda _t: [])
    assert deanonymize(result.text, result.mapping) == _RESUME


def test_no_global_state_accumulates() -> None:
    """PII-3: redact is pure w.r.t. the mapping — two calls don't bleed into each other."""
    a = redact("Aarav Sharma", known_pii=["Aarav Sharma"], ner=lambda _t: [])
    b = redact("Vikram Rao", known_pii=["Vikram Rao"], ner=lambda _t: [])
    assert "Aarav" not in b.mapping.values()
    assert a.mapping != b.mapping


def test_longest_first_avoids_substring_fragments() -> None:
    """PII-1: a full name is redacted before its first-name substring (no dangling fragments)."""
    result = redact(
        "Aarav Sharma met Aarav.", known_pii=["Aarav Sharma", "Aarav"], ner=lambda _t: []
    )
    assert "Aarav" not in result.text
    # Both map cleanly; de-anon restores exactly.
    assert deanonymize(result.text, result.mapping) == "Aarav Sharma met Aarav."


def test_result_is_typed_and_generic() -> None:
    """PII-4: the surface is plain str + list[str] (no ingest types) and returns a typed model."""
    result = redact("hello", known_pii=[], ner=lambda _t: [])
    assert isinstance(result, RedactionResult)
    assert result.text == "hello"
    assert result.mapping == {}
