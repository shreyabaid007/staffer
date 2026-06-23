"""Query-time PII wiring at the CLI composition root (c-003 T-005; AD-097). R-05/R-11.

The score predictor is wrapped so each candidate's known identity (resolved from the vault by
``candidate_id`` = ``Candidate.email``) is active in :func:`pii_context` for the LLM call. These
tests prove the wrapper sets the right context (unit) and that, composed with the real
``PseudonymisedLM`` seam, a planted name in the candidate's de-anonymised gold free-text never
reaches the provider.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import dspy
import pytest

from dsm.cli.commands import _pii_aware_score_predictor
from dsm.match.models import ScoreExtraction
from dsm.models import (
    Candidate,
    CandidateSource,
    FeedbackEntry,
    FeedbackSignals,
    FeedbackSource,
    FreeNow,
    Location,
    ProficiencyLevel,
    Skill,
    TargetProfileScorecard,
)
from dsm.pii.pseudonymised_lm import PseudonymisedLM
from dsm.pii.vault import FileVault
from dsm.pii.vault import candidate_id as derive_cid
from tests.fixtures import role_01

_MODEL = "openrouter/anthropic/claude-sonnet-4-6"


def _candidate(cid: str, feedback_text: str, summary: str) -> Candidate:
    """A serving Candidate as GoldCandidateStore hydrates it: email == candidate_id (AD-091)."""
    return Candidate(
        email=cid,
        name=cid,
        location=Location(city="Bengaluru"),
        availability=FreeNow(),
        skills=[Skill(name="kotlin", proficiency=ProficiencyLevel.ADVANCED)],
        feedback=FeedbackSignals(
            entries=[FeedbackEntry(source=FeedbackSource.CLIENT, text=feedback_text)]
        ),
        source=CandidateSource.BEACH,
        profile_summary=summary,
    )


_, _SCORECARD = role_01()  # a fully-valid scorecard; the score base only reads the candidate


def test_wrapper_sets_pii_context_from_vault(tmp_path: Path) -> None:
    """R-05: the wrapper resolves the candidate's identity and activates it in pii_context."""
    cid = derive_cid("priya@acme.example")
    vault = FileVault(tmp_path / "vault.json")
    vault.put_identity(cid, "Priya Nair", "priya@acme.example")

    seen: dict[str, Any] = {}

    def spy_base(_sc: TargetProfileScorecard, _c: Candidate) -> ScoreExtraction:
        from dsm.pii.pseudonymised_lm import _KNOWN_PII

        seen["known"] = _KNOWN_PII.get()
        return ScoreExtraction()

    wrapped = _pii_aware_score_predictor(spy_base, vault)
    wrapped(_SCORECARD, _candidate(cid, "Great work.", "Senior dev."))

    assert seen["known"] == ["Priya Nair", "priya@acme.example"]


def test_wrapper_empty_context_when_identity_missing(tmp_path: Path) -> None:
    """R-05/R-07: a candidate with no vault entry → empty known list (NER-only), never a crash."""
    seen: dict[str, Any] = {}

    def spy_base(_sc: TargetProfileScorecard, _c: Candidate) -> ScoreExtraction:
        from dsm.pii.pseudonymised_lm import _KNOWN_PII

        seen["known"] = _KNOWN_PII.get()
        return ScoreExtraction()

    wrapped = _pii_aware_score_predictor(spy_base, FileVault(tmp_path / "vault.json"))
    wrapped(_SCORECARD, _candidate("cid:unknown", "Solid.", "Dev."))

    assert seen["known"] == []  # set (not None) → engages NER, leak-scan no-ops


def test_planted_name_in_gold_text_never_reaches_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-05/R-10: composed with the real PseudonymisedLM seam, the candidate's name is stripped.

    Simulates what ``make_score_predictor`` does (build messages from the candidate's free-text and
    call the LM), but with the provider call captured — so we assert on the exact outbound text.
    """
    captured: dict[str, Any] = {}

    def fake_base_call(
        self: dspy.LM,
        prompt: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        captured["messages"] = messages
        return ["ok"]

    monkeypatch.setattr(dspy.LM, "__call__", fake_base_call)

    cid = derive_cid("priya@acme.example")
    vault = FileVault(tmp_path / "vault.json")
    vault.put_identity(cid, "Priya Nair", "priya@acme.example")
    lm = PseudonymisedLM(model=_MODEL, ner=lambda _t: [])

    def base(_sc: TargetProfileScorecard, candidate: Candidate) -> ScoreExtraction:
        # Mirror make_score_predictor: the candidate's de-anonymised free-text goes to the LM.
        text = candidate.profile_summary or ""
        text += " " + " ".join(e.text for e in candidate.feedback.entries)
        with dspy.context(lm=lm):
            lm(messages=[{"role": "user", "content": text}])
        return ScoreExtraction()

    wrapped = _pii_aware_score_predictor(base, vault)
    cand = _candidate(cid, "Priya Nair delivered the payments platform.", "Led by Priya Nair.")
    wrapped(_SCORECARD, cand)

    sent = captured["messages"][-1]["content"]
    assert "Priya Nair" not in sent  # the provider never saw the planted name
    assert "[[PII_" in sent  # the name was replaced by a known-PII placeholder


def test_context_does_not_leak_across_candidates(tmp_path: Path) -> None:
    """R-04: each candidate's context is scoped — it resets between wrapped calls."""
    cid = derive_cid("priya@acme.example")
    vault = FileVault(tmp_path / "vault.json")
    vault.put_identity(cid, "Priya Nair", "priya@acme.example")

    from dsm.pii.pseudonymised_lm import _KNOWN_PII

    seen: list[Any] = []

    def spy_base(_sc: TargetProfileScorecard, _c: Candidate) -> ScoreExtraction:
        seen.append(_KNOWN_PII.get())
        return ScoreExtraction()

    wrapped = _pii_aware_score_predictor(spy_base, vault)
    wrapped(_SCORECARD, _candidate(cid, "x", "y"))  # has identity
    wrapped(_SCORECARD, _candidate("cid:other", "x", "y"))  # no identity
    assert seen == [["Priya Nair", "priya@acme.example"], []]
    assert _KNOWN_PII.get() is None  # fully reset outside the wrapper


def test_dsm_match_has_no_pii_import() -> None:
    """R-11: the pure pipeline never imports dsm.pii — wiring lives only at the CLI."""
    import importlib
    import pkgutil

    import dsm.match

    offenders: list[str] = []
    for mod in pkgutil.walk_packages(dsm.match.__path__, prefix="dsm.match."):
        source = importlib.import_module(mod.name)
        for attr in vars(source).values():
            module_name = getattr(attr, "__module__", "")
            if isinstance(module_name, str) and module_name.startswith("dsm.pii"):
                offenders.append(f"{mod.name} → {module_name}")
    assert not offenders, f"dsm.match must not depend on dsm.pii: {offenders}"
