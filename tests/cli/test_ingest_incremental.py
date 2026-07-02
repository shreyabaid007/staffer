"""Incremental ingest correctness — the c-011 two-run scenario (AD-XXX; FR-5-AC-1..5).

Run 1 ingests a supply sheet + a feedback doc. Run 2 edits ONLY the supply sheet (add one
candidate, remove one). The unchanged feedback doc must be merged back into gold (G-1
regression — enrichment is never dropped by a partial re-land) with **zero** enrich-LLM
calls (the FileEnrichCache hit). A third untouched run must be byte-stable (gold write
gate). The index side of FR-5-AC-4 — re-embed skipped on an unchanged
``(gold_hash, model_version)`` — is covered by the existing AD-082 indexer tests.

Hermetic: ``enrich_feedback`` is monkeypatched at its module (the ingest body imports it at
call time), so no NER model, no LLM, no network. The fake still routes through the cache
wiring in ``commands.ingest`` — which is what this test exercises.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import dsm.ingest.enrich as enrich_mod
from dsm.cli.main import app
from dsm.models import EvidenceCitation, EvidenceSource

_runner = CliRunner()

_BEACH_V1 = (
    b"Beach - as of 2026-06-01 (synthetic)\n"
    b"Name,Email,Grade,Key Skills,Location,Chennai-open\n"
    b'Priya,priya@acme.example,Lead Consultant,"Java, Kotlin",Bengaluru,Yes\n'
    b"Arjun,arjun@acme.example,Senior Consultant,Python,Pune,No\n"
)
# v2: Arjun removed, Neha added — Priya's row unchanged.
_BEACH_V2 = (
    b"Beach - as of 2026-07-02 (synthetic)\n"
    b"Name,Email,Grade,Key Skills,Location,Chennai-open\n"
    b'Priya,priya@acme.example,Lead Consultant,"Java, Kotlin",Bengaluru,Yes\n'
    b"Neha,neha@acme.example,Senior Consultant,React,Mumbai,Yes\n"
)
_FEEDBACK = (
    b"# Feedback - Priya\n"
    b"email: priya@acme.example\n"
    b"\n"
    b"## Project feedback - Acme\n"
    b"Priya delivered the payments workstream without concerns.\n"
)


@pytest.fixture()
def counting_enrich(monkeypatch) -> list[str]:
    """Replace the real feedback enrichment with a counting fake (hermetic seam)."""
    calls: list[str] = []

    def fake_enrich_feedback(record, *, known_pii, predict, run_id="", ner=None, metrics=None):
        calls.append(record.candidate_id)
        return enrich_mod.FeedbackExtraction(
            confirmed_skills=["kotlin"],
            sentiment="positive",
            summary="delivered the payments workstream",
            evidence=EvidenceCitation(
                source=EvidenceSource.FEEDBACK,
                text="delivered the payments workstream",
            ),
        )

    monkeypatch.setattr(enrich_mod, "enrich_feedback", fake_enrich_feedback)
    # The lazy predictor builder must never run on an all-hit pass; make it loud if it does.
    return calls


def _ingest(tmp_path: Path):
    return _runner.invoke(
        app,
        [
            "ingest",
            "--raw-dir",
            str(tmp_path / "raw"),
            "--bronze-dir",
            str(tmp_path / "bronze"),
            "--silver-dir",
            str(tmp_path / "silver"),
            "--gold-dir",
            str(tmp_path / "gold"),
            "--run-id",
            "run-incr",
        ],
    )


def _gold_by_cid(tmp_path: Path) -> dict[str, dict]:
    out = {}
    for p in (tmp_path / "gold").glob("*.json"):
        doc = json.loads(p.read_text())
        out[doc["candidate_id"]] = doc
    return out


def _cid(email: str) -> str:
    from dsm.pii.vault import candidate_id

    return candidate_id(email)


def test_two_run_incremental_scenario(tmp_path: Path, counting_enrich: list[str]) -> None:
    raw = tmp_path / "raw"
    (raw / "supply").mkdir(parents=True)
    (raw / "feedback").mkdir(parents=True)
    (raw / "supply" / "beach.csv").write_bytes(_BEACH_V1)
    (raw / "feedback" / "priya.md").write_bytes(_FEEDBACK)

    # ── Run 1: full ingest — one enrich LLM call (the feedback doc), cache cold.
    r1 = _ingest(tmp_path)
    assert r1.exit_code == 0, r1.output
    assert "llm_calls=1 cache_hits=0" in r1.output
    assert len(counting_enrich) == 1
    gold1 = _gold_by_cid(tmp_path)
    priya1 = gold1[_cid("priya@acme.example")]
    assert priya1["feedback"], "run 1 must merge the enriched feedback into gold"

    # ── Run 2: edit ONLY the supply sheet — add Neha, remove Arjun.
    (raw / "supply" / "beach.csv").write_bytes(_BEACH_V2)
    r2 = _ingest(tmp_path)
    assert r2.exit_code == 0, r2.output

    # FR-5-AC-2 / FR-5-AC-4: zero LLM calls — the unchanged feedback is a cache hit.
    assert "llm_calls=0 cache_hits=1" in r2.output
    assert len(counting_enrich) == 1  # no new fake-enrich invocation either

    gold2 = _gold_by_cid(tmp_path)
    priya2 = gold2[_cid("priya@acme.example")]
    # G-1 regression (FR-5-AC-1): the partial re-land must NOT drop Priya's enrichment.
    assert priya2["feedback"], "supply-only edit dropped the feedback enrichment from gold"
    assert priya2["feedback"][0]["summary"] == "delivered the payments workstream"
    assert priya2["is_tombstoned"] is False
    # Roster change applied: Neha live, Arjun tombstoned (content preserved, flag set).
    assert gold2[_cid("neha@acme.example")]["is_tombstoned"] is False
    assert gold2[_cid("arjun@acme.example")]["is_tombstoned"] is True
    assert "tombstones  : 1" in r2.output

    # ── Run 3: nothing changed — write gate: byte-stable, zero writes, zero LLM calls.
    before = {p.name: p.read_text() for p in (tmp_path / "gold").glob("*.json")}
    r3 = _ingest(tmp_path)
    assert r3.exit_code == 0, r3.output
    assert "updated=0" in r3.output
    assert "llm_calls=0 cache_hits=1" in r3.output
    after = {p.name: p.read_text() for p in (tmp_path / "gold").glob("*.json")}
    assert after == before


def test_prompt_version_bump_invalidates_cache(
    tmp_path: Path, counting_enrich: list[str], monkeypatch
) -> None:
    """FR-5-AC-2: a prompt_version bump re-extracts (the §11 rule, now enforced)."""
    raw = tmp_path / "raw"
    (raw / "supply").mkdir(parents=True)
    (raw / "feedback").mkdir(parents=True)
    (raw / "supply" / "beach.csv").write_bytes(_BEACH_V1)
    (raw / "feedback" / "priya.md").write_bytes(_FEEDBACK)

    assert _ingest(tmp_path).exit_code == 0
    assert len(counting_enrich) == 1

    import copy

    from dsm.config import load_config

    # Deep-copy: load_config() is lru_cached and returns the shared dict — mutating it would
    # leak the bumped prompt_version into every later test in the session.
    bumped = copy.deepcopy(load_config())
    bumped["enrich"]["prompt_version"] = "enrich-v2-test"
    monkeypatch.setattr("dsm.config.load_config", lambda: bumped)

    r2 = _ingest(tmp_path)
    assert r2.exit_code == 0, r2.output
    assert "llm_calls=1" in r2.output  # cache key changed → re-extracted
    assert len(counting_enrich) == 2
