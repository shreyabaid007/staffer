"""Golden cases + cassette LM for deterministic eval (c-002, AD-095 Tier 1/2).

Each ``GoldenCase`` binds a seed role fixture to cassette responses + expected
outcomes. The ``CassetteLM`` replays recorded ``clarify``/``score`` responses
so invariant and signature-regression evals run without network or API keys.

Cassette discipline:
- Located under ``tests/fixtures/cassettes/<case_id>/``.
- Keyed by ``(case_id, signature, prompt_hash, model_version)``.
- Staleness is loud: a key mismatch → ``RuntimeError`` (never skip/fallback).
- Regeneration: ``make eval-record`` / ``dsm.eval.record``.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from dsm.match.models import ScorecardClarification, ScoreExtraction
from dsm.models import (
    Candidate,
    EvidenceCitation,
    EvidenceSource,
    OpenRole,
    TargetProfileScorecard,
)
from dsm.pii.vault import candidate_id

_CASSETTE_ROOT = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "cassettes"


@dataclass(frozen=True)
class CassetteKey:
    """Identifies a recorded LLM response for replay (AD-095)."""

    case_id: str
    signature: str
    prompt_hash: str
    model_version: str


@dataclass(frozen=True)
class GoldenCase:
    """A seed role + candidates bound to cassette responses and expected outcome."""

    case_id: str
    candidates: list[Candidate]
    scorecard: TargetProfileScorecard
    cassette_dir: Path
    expected_type: Literal["shortlist", "no_match"]
    expected_ranked_ids: list[str] | None = None
    expected_no_match: bool = False


def _prompt_hash(signature_cls: type) -> str:
    """SHA-256 prefix of the DSPy Signature class source (docstring + fields)."""
    src = inspect.getsource(signature_cls)
    return hashlib.sha256(src.encode()).hexdigest()[:16]


def current_model_version() -> str:
    """The reasoning LLM id from config (single source of truth for cassette keys)."""
    from dsm.config import load_config

    return str(load_config()["models"]["reasoning_llm"])


def load_cassette(case_id: str, signature: str, *, cassette_dir: Path | None = None) -> dict:
    """Load a cassette JSON file and return its parsed content."""
    root = cassette_dir or (_CASSETTE_ROOT / case_id)
    path = root / f"{signature}.json"
    if not path.exists():
        raise FileNotFoundError(f"Cassette not found: {path}")
    return json.loads(path.read_text())


def validate_cassette_freshness(case_id: str, *, cassette_dir: Path | None = None) -> None:
    """Raise if any cassette's key doesn't match the current prompt/model version.

    A key mismatch means the DSPy Signature changed or the model was bumped —
    the cassette must be re-recorded before evals can trust it.
    """
    from dsm.match.clarify import RoleClarification
    from dsm.match.score import CandidateScoring

    model = current_model_version()
    expected_hashes = {
        "clarify": _prompt_hash(RoleClarification),
        "score": _prompt_hash(CandidateScoring),
    }

    root = cassette_dir or (_CASSETTE_ROOT / case_id)
    for sig_name, expected_hash in expected_hashes.items():
        path = root / f"{sig_name}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        key = data.get("key", {})
        if key.get("prompt_hash") != expected_hash:
            raise RuntimeError(
                f"Stale cassette {path}: prompt_hash "
                f"{key.get('prompt_hash')!r} != current {expected_hash!r} "
                "— re-record with `make eval-record`"
            )
        if key.get("model_version") != model:
            raise RuntimeError(
                f"Stale cassette {path}: model_version "
                f"{key.get('model_version')!r} != current {model!r} "
                "— re-record with `make eval-record`"
            )


def pseudonymise_candidates(
    candidates: list[Candidate],
) -> tuple[list[Candidate], list[str]]:
    """Mirror GoldCandidateStore: replace email/name with candidate_id.

    Returns ``(pseudonymised_candidates, known_pii)`` where ``known_pii`` is the
    original names + emails for leak-scanning. Uses ``dsm.pii.vault.candidate_id``
    for a deterministic HMAC derivation (test-stable via the env-set key).
    """
    known_pii: list[str] = []
    pseudonymised: list[Candidate] = []
    for c in candidates:
        known_pii.append(c.name)
        known_pii.append(c.email)
        cid = candidate_id(c.email)
        pseudonymised.append(c.model_copy(update={"email": cid, "name": cid}))
    known_pii = [p for p in known_pii if p.strip()]
    return pseudonymised, known_pii


@dataclass
class SeamCapture:
    """Wraps a predict callable to capture its inputs into a ``SeamInputs``."""

    clarify_inputs: list[dict[str, Any]] = field(default_factory=list)
    score_inputs: list[dict[str, Any]] = field(default_factory=list)


class CassetteLM:
    """Replays recorded clarify/score responses for a golden case (AD-095).

    Not a single object — produces two separate callables matching the existing
    seams. Keyed by ``(case_id, signature)``. If a call doesn't match a recorded
    response, raises ``RuntimeError("stale cassette — re-record")``.
    """

    def __init__(
        self,
        case_id: str,
        cassette_dir: Path | None = None,
        *,
        capture: SeamCapture | None = None,
    ) -> None:
        self._case_id = case_id
        self._dir = cassette_dir or (_CASSETTE_ROOT / case_id)
        self._capture = capture
        self._clarify_data: dict | None = None
        self._score_data: dict | None = None

    def _load_clarify(self) -> dict:
        if self._clarify_data is None:
            self._clarify_data = load_cassette(self._case_id, "clarify", cassette_dir=self._dir)
        return self._clarify_data

    def _load_score(self) -> dict:
        if self._score_data is None:
            self._score_data = load_cassette(self._case_id, "score", cassette_dir=self._dir)
        return self._score_data

    def clarify_predict(self):
        """Return a ``ClarifyPredictor`` that replays the recorded clarify response."""
        data = self._load_clarify()
        response = data["response"]
        capture = self._capture

        def _predict(role: OpenRole) -> ScorecardClarification:
            if capture is not None:
                capture.clarify_inputs.append({"title": role.title, "role_id": role.role_id})
            return ScorecardClarification(**response)

        return _predict

    def score_predict(self):
        """Return a ``ScorePredictor`` that replays recorded per-candidate responses.

        Candidates are looked up by email (pre- or post-pseudonymisation). A miss
        means the cassette doesn't cover this candidate — ``RuntimeError``.
        """
        data = self._load_score()
        responses = data["responses"]
        capture = self._capture
        email_to_original = self._email_to_original_map(responses)

        def _predict(scorecard: TargetProfileScorecard, cand: Candidate) -> ScoreExtraction:
            if capture is not None:
                capture.score_inputs.append(
                    {"candidate_email": cand.email, "role_id": scorecard.role_id}
                )
            resp = responses.get(cand.email) or responses.get(
                email_to_original.get(cand.email, "")
            )
            if resp is None:
                raise RuntimeError(
                    f"Stale cassette: no recorded score response for "
                    f"{cand.email!r} in {self._case_id} "
                    "— re-record with `make eval-record`"
                )
            evidence = [
                EvidenceCitation(source=EvidenceSource(e["source"]), text=e["text"])
                for e in resp.get("evidence", [])
            ]
            return ScoreExtraction(
                skill_match_score=resp["skill_match_score"],
                feedback_score=resp["feedback_score"],
                narrative=resp["narrative"],
                evidence=evidence,
            )

        return _predict

    @staticmethod
    def _email_to_original_map(responses: dict) -> dict[str, str]:
        """Build a cid→original-email reverse map for pseudonymised lookups."""
        mapping: dict[str, str] = {}
        for email in responses:
            try:
                cid = candidate_id(email)
                mapping[cid] = email
            except (RuntimeError, ValueError):
                pass
        return mapping


def load_golden_cases(*, cassette_root: Path | None = None) -> list[GoldenCase]:
    """Load the three golden cases from seed fixtures.

    Candidates are returned as-is (not pseudonymised) — the caller (the test
    runner) calls ``pseudonymise_candidates`` before feeding them to ``run_match``
    so the eval mirrors the real pipeline.
    """
    from tests.fixtures import role_01, role_02, role_03

    root = cassette_root or _CASSETTE_ROOT
    cases: list[GoldenCase] = []

    candidates_01, scorecard_01 = role_01()
    cases.append(
        GoldenCase(
            case_id="ROLE-01",
            candidates=candidates_01,
            scorecard=scorecard_01,
            cassette_dir=root / "ROLE-01",
            expected_type="shortlist",
        )
    )

    candidates_02, scorecard_02 = role_02()
    cases.append(
        GoldenCase(
            case_id="ROLE-02",
            candidates=candidates_02,
            scorecard=scorecard_02,
            cassette_dir=root / "ROLE-02",
            expected_type="shortlist",
        )
    )

    candidates_03, scorecard_03 = role_03()
    cases.append(
        GoldenCase(
            case_id="ROLE-03",
            candidates=candidates_03,
            scorecard=scorecard_03,
            cassette_dir=root / "ROLE-03",
            expected_type="no_match",
            expected_no_match=True,
        )
    )

    return cases
