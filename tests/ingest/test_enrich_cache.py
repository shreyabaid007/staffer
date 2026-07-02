"""FileEnrichCache unit tests (c-011 T-001; FR-5-AC-2)."""

from __future__ import annotations

from pathlib import Path

from dsm.ingest.enrich_cache import FileEnrichCache, enrich_cache_key
from dsm.ingest.models import FeedbackExtraction, ProfileSummaryExtraction
from dsm.models import EvidenceCitation, EvidenceSource

_KEY_ARGS = {
    "candidate_id": "cid:abc",
    "source_hash": "sha256:deadbeef",
    "raw_text": "resume body text",
    "prompt_version": "enrich-v1",
    "model_version": "openrouter/anthropic/claude-sonnet-4-6",
}


def _resume() -> ProfileSummaryExtraction:
    return ProfileSummaryExtraction(employers=["Acme"], domains=["payments"])


def _feedback() -> FeedbackExtraction:
    return FeedbackExtraction(
        confirmed_skills=["kotlin"],
        sentiment="positive",
        summary="solid delivery",
        evidence=EvidenceCitation(source=EvidenceSource.FEEDBACK, text="solid delivery"),
    )


class TestKeyDerivation:
    def test_deterministic(self) -> None:
        assert enrich_cache_key(**_KEY_ARGS) == enrich_cache_key(**_KEY_ARGS)

    def test_every_component_changes_the_key(self) -> None:
        base = enrich_cache_key(**_KEY_ARGS)
        for field, bumped in [
            ("candidate_id", "cid:other"),
            ("source_hash", "sha256:cafebabe"),
            ("raw_text", "different text"),
            ("prompt_version", "enrich-v2"),
            ("model_version", "other-model"),
        ]:
            assert enrich_cache_key(**{**_KEY_ARGS, field: bumped}) != base, field

    def test_no_delimiter_collision(self) -> None:
        # "ab"+"c" must not collide with "a"+"bc" across adjacent components.
        a = enrich_cache_key(**{**_KEY_ARGS, "candidate_id": "ab", "source_hash": "c"})
        b = enrich_cache_key(**{**_KEY_ARGS, "candidate_id": "a", "source_hash": "bc"})
        assert a != b


class TestRoundTrip:
    def test_resume_round_trip(self, tmp_path: Path) -> None:
        cache = FileEnrichCache(tmp_path / "enrich_cache")
        key = enrich_cache_key(**_KEY_ARGS)
        assert cache.get_resume(key) is None
        cache.put_resume(key, _resume(), key_material={"source_hash": "sha256:deadbeef"})
        assert cache.get_resume(key) == _resume()

    def test_feedback_round_trip(self, tmp_path: Path) -> None:
        cache = FileEnrichCache(tmp_path / "enrich_cache")
        key = enrich_cache_key(**{**_KEY_ARGS, "raw_text": "feedback item 2"})
        assert cache.get_feedback(key) is None
        cache.put_feedback(key, _feedback(), key_material={})
        assert cache.get_feedback(key) == _feedback()

    def test_kind_mismatch_is_a_miss(self, tmp_path: Path) -> None:
        cache = FileEnrichCache(tmp_path)
        key = enrich_cache_key(**_KEY_ARGS)
        cache.put_resume(key, _resume(), key_material={})
        assert cache.get_feedback(key) is None

    def test_corrupt_entry_is_a_miss(self, tmp_path: Path) -> None:
        cache = FileEnrichCache(tmp_path)
        key = enrich_cache_key(**_KEY_ARGS)
        (tmp_path / f"{key}.json").write_text("{not json", encoding="utf-8")
        assert cache.get_resume(key) is None

    def test_no_tmp_file_left_behind(self, tmp_path: Path) -> None:
        cache = FileEnrichCache(tmp_path)
        key = enrich_cache_key(**_KEY_ARGS)
        cache.put_resume(key, _resume(), key_material={})
        assert not list(tmp_path.glob("*.tmp"))
