"""Gold-store tests (a-003 T-008). GS-1/GS-2/GS-4."""

from __future__ import annotations

from pathlib import Path

from dsm.ingest.goldstore import gold_hash, list_gold_ids, read_gold, write_gold
from dsm.ingest.models import Confidence, GoldCandidate, Grade, MergedSkill, Sourced
from dsm.models import FreeNow


def _gold(candidate_id: str = "cid:abc", *, skill: str = "kotlin") -> GoldCandidate:
    draft = GoldCandidate(
        candidate_id=candidate_id,
        name_vault_ref=f"name:{candidate_id}",
        email_vault_ref=f"email:{candidate_id}",
        grade=Sourced(value=Grade.LEAD_CONSULTANT),
        availability=Sourced(value=FreeNow()),
        skills=[MergedSkill(name=skill, confidence=Confidence.MEDIUM)],
        gold_hash="",
        merge_version="merge-v1",
        prompt_version="enrich-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )
    return draft.model_copy(update={"gold_hash": gold_hash(draft)})


def test_write_read_round_trip(tmp_path: Path) -> None:
    """GS-1: persist then read back yields an equal entity at gold/<hex>.json."""
    gold = _gold()
    dest = write_gold(gold, tmp_path)
    assert dest.name == "abc.json"  # cid: prefix stripped for the filename
    assert read_gold("cid:abc", tmp_path) == gold


def test_write_is_idempotent(tmp_path: Path) -> None:
    write_gold(_gold(), tmp_path)
    write_gold(_gold(), tmp_path)  # rewrite identical bytes, no error
    assert list_gold_ids(tmp_path) == {"cid:abc"}


def test_gold_hash_change_sensitive(tmp_path: Path) -> None:
    """GS-2: a content change yields a different gold_hash (drives re-index)."""
    assert gold_hash(_gold(skill="kotlin")) != gold_hash(_gold(skill="java"))


def test_list_ids_is_prior_set(tmp_path: Path) -> None:
    """RC-1 input: the on-disk id set is the prior set for reconciliation."""
    write_gold(_gold("cid:aaa"), tmp_path)
    write_gold(_gold("cid:bbb"), tmp_path)
    assert list_gold_ids(tmp_path) == {"cid:aaa", "cid:bbb"}
    assert list_gold_ids(tmp_path / "missing") == set()


def test_gold_has_vault_refs_not_raw_identity(tmp_path: Path) -> None:
    """GS-4: persisted gold carries vault refs only — no raw identity field exists."""
    write_gold(_gold(), tmp_path)
    raw = (tmp_path / "abc.json").read_text(encoding="utf-8")
    assert '"name_vault_ref":"name:cid:abc"' in raw.replace(" ", "")
    assert '"email_vault_ref":"email:cid:abc"' in raw.replace(" ", "")
    # No raw email and no bare top-level name/email identity keys.
    assert "@" not in raw
    assert '"email":' not in raw
