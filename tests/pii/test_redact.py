"""Tests for the generic PII redactor (a-003 T-002; AD-069/AD-078). PII-1..4.

c-003 (T-001) adds the multi-fragment ``Redactor``/``redact_fragments`` (R-09): one stable
mapping across a prompt + several messages, so the same surface form yields the same placeholder
everywhere in one LLM call.
"""

from __future__ import annotations

from dsm.pii.redact import (
    NerSpan,
    RedactionResult,
    Redactor,
    deanonymize,
    redact,
    redact_fragments,
)

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


# ── c-003 T-001: multi-fragment Redactor (R-09) ──────────────────────────────────────────────


def test_known_placeholder_stable_across_fragments() -> None:
    """R-09: the same known identifier gets the SAME PII placeholder in every fragment."""
    redacted, mapping = redact_fragments(
        ["Aarav Sharma joined.", "We later promoted Aarav Sharma."],
        known_pii=["Aarav Sharma"],
        ner=lambda _t: [],
    )
    assert "Aarav Sharma" not in " ".join(redacted)
    # One mapping entry, reused — not two clashing placeholders for one identifier.
    assert list(mapping.values()) == ["Aarav Sharma"]
    placeholder = next(iter(mapping))
    assert all(placeholder in frag for frag in redacted)


def test_ner_placeholder_reused_for_repeat_span_across_fragments() -> None:
    """R-09: an NER span seen in fragment 1 reuses its placeholder when it recurs in fragment 2."""

    def fake_ner(text: str) -> list[NerSpan]:
        return [("Priya Nair", "PERSON")] if "Priya Nair" in text else []

    redacted, mapping = redact_fragments(
        ["Reviewed by Priya Nair.", "Priya Nair signed off again."],
        known_pii=[],
        ner=fake_ner,
    )
    assert "Priya Nair" not in " ".join(redacted)
    # Exactly one NER placeholder, used in both fragments (no NER_0 vs NER_1 collision).
    assert list(mapping.values()) == ["Priya Nair"]
    placeholder = next(iter(mapping))
    assert all(placeholder in frag for frag in redacted)


def test_fragments_deanonymize_round_trip() -> None:
    """R-09: the shared mapping restores every fragment unambiguously."""

    def fake_ner(text: str) -> list[NerSpan]:
        return [("Priya Nair", "PERSON")] if "Priya Nair" in text else []

    originals = ["Aarav Sharma worked with Priya Nair.", "Priya Nair praised Aarav Sharma."]
    redacted, mapping = redact_fragments(originals, known_pii=["Aarav Sharma"], ner=fake_ner)
    assert [deanonymize(frag, mapping) for frag in redacted] == originals


def test_redactor_is_byte_identical_run_to_run() -> None:
    """R-09: a fixed (fragments, known_pii, ner) is deterministic across independent Redactors."""

    def fake_ner(text: str) -> list[NerSpan]:
        return [(s, "PERSON") for s in ("Priya Nair", "Vikram Rao") if s in text]

    frags = ["Aarav Sharma, Priya Nair, Vikram Rao.", "Vikram Rao met Priya Nair."]
    first = redact_fragments(frags, known_pii=["Aarav Sharma"], ner=fake_ner)
    second = redact_fragments(frags, known_pii=["Aarav Sharma"], ner=fake_ner)
    assert first == second


def test_redactor_class_exposes_running_mapping() -> None:
    """R-09: the Redactor mapping accumulates across explicit redact() calls."""
    r = Redactor(["Aarav Sharma"], ner=lambda _t: [])
    out1 = r.redact("Aarav Sharma here.")
    out2 = r.redact("Still Aarav Sharma.")
    assert "Aarav Sharma" not in out1 + out2
    assert list(r.mapping.values()) == ["Aarav Sharma"]


# ── c-003 review hardening (adversarial review fixes) ────────────────────────────────────────


def test_ner_mixed_casing_redacts_both_and_round_trips_exactly() -> None:
    """Review[2]: distinct casings each get a placeholder; de-anon restores each verbatim."""

    def fake_ner(text: str) -> list[NerSpan]:
        return [(s, "PERSON") for s in ("John", "JOHN") if s in text]

    redacted, mapping = redact_fragments(
        ["Worked with John.", "JOHN is skilled."], known_pii=[], ner=fake_ner
    )
    joined = " ".join(redacted)
    assert "John" not in joined and "JOHN" not in joined  # both casings redacted (no leak)
    # Exact verbatim round-trip per fragment (no casing drift).
    assert [deanonymize(f, mapping) for f in redacted] == ["Worked with John.", "JOHN is skilled."]


def test_deanonymize_resolves_nested_placeholder_value() -> None:
    """Review[6]: a mapped value containing another placeholder is fully restored, any order."""
    mapping = {"[[NER_0]]": "[[PII_0]] Smith", "[[PII_0]]": "Aarav"}
    assert deanonymize("Reviewed by [[NER_0]].", mapping) == "Reviewed by Aarav Smith."


def test_known_pass_does_not_corrupt_its_own_placeholders() -> None:
    """Finding[2]: a short id must not match INSIDE a placeholder a prior id inserted.

    ``"Ii"`` case-insensitively matches the ``II`` in ``[[PII_0]]`` — a sequential pass would
    corrupt it. The single-pass alternation over the original text prevents that.
    """
    result = redact(
        "Aarav Pii reviewed the Ii module.",
        known_pii=["Aarav Pii", "Ii"],
        ner=lambda _t: [],
    )
    assert "Aarav Pii" not in result.text
    assert "[[P[[" not in result.text and "]]_0]]" not in result.text  # no nested/corrupt token
    assert deanonymize(result.text, result.mapping) == "Aarav Pii reviewed the Ii module."


def test_dedupe_known_skips_non_str_entries() -> None:
    """Review[5]: a non-str entry in known_pii is dropped, not crashed on."""
    result = redact(
        "Aarav Sharma here.",
        known_pii=["Aarav Sharma", None, 123],  # type: ignore[list-item]
        ner=lambda _t: [],
    )
    assert "Aarav Sharma" not in result.text
    assert list(result.mapping.values()) == ["Aarav Sharma"]
