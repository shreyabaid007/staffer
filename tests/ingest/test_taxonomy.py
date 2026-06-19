"""Tests for the skill taxonomy (a-002 T-003; TX-1/TX-2)."""

from dsm.ingest.taxonomy import Taxonomy, load_taxonomy


def _taxonomy() -> Taxonomy:
    return Taxonomy({"kotlin": "kotlin", "react.js": "react", "react": "react"})


def test_known_alias_maps_to_canonical() -> None:
    name, unmapped = _taxonomy().canonical_skill("React.js")
    assert name == "react"
    assert unmapped is False


def test_canonical_id_resolves_to_itself() -> None:
    name, unmapped = _taxonomy().canonical_skill("kotlin")
    assert (name, unmapped) == ("kotlin", False)


def test_lookup_is_case_and_whitespace_insensitive() -> None:
    name, unmapped = _taxonomy().canonical_skill("  KOTLIN ")
    assert (name, unmapped) == ("kotlin", False)


def test_unknown_skill_is_flagged_and_kept_verbatim() -> None:
    name, unmapped = _taxonomy().canonical_skill("  COBOL ")
    assert name == "COBOL"  # trimmed, surface form preserved for the review queue
    assert unmapped is True


def test_real_taxonomy_loads_and_resolves_a_seed_alias() -> None:
    taxonomy = load_taxonomy()
    assert taxonomy.canonical_skill("k8s") == ("kubernetes", False)
    assert taxonomy.canonical_skill("Amazon Web Services") == ("aws", False)
