"""Natural-language intake (c-006; AD-XXX/AD-XXY; § A1/A3) — free-text request → ``OpenRole``.

The prose front door's parser, the NL counterpart of ``demand.py::parse_demand`` (which parses the
demand CSV). One LLM call interprets the request; **Python** assembles + validates the result into
the **existing** frozen ``OpenRole`` — the same object the CSV path produces, so the pipeline below
is untouched.

Two halves, one clean boundary:

- **``make_intake_predictor(lm)``** — a bounded, single-shot DSPy ``Predict`` over the injected LM
  (``PseudonymisedLM`` at the CLI, pass-through; the ``predict`` seam is mocked in tests). It takes
  the prose **and** today's date and emits a typed ``RoleIntake``. NOT a chat loop / ReAct /
  ``dspy.Refine`` (AD-001 non-agentic; ``Refine`` runs at temp 1.0 and would break determinism).
- **``assemble_role(...)``** — pure, LLM-free. Validates the LLM-resolved start date
  deterministically (calendar + a plausibility window) **before** it can reach the availability
  gate (AD-XXY), **derives** ``co_location_required`` in Python (never an LLM output — AD-002 /
  FR-8), forces each skill's ``depth`` from its bucket, and returns either a ready ``OpenRole``
  or a typed ``ClarificationNeeded`` naming the missing required gate field(s).

Determinism (AD-066): the parse is keyed by ``intake_cache_key(prose, today, model_id,
prompt_version)`` — a model/prompt-version bump changes the key and forces a re-parse. The module
imports only ``dspy`` + ``dsm.config`` + ``dsm.models`` + ``dsm.match.models`` + ``structlog`` +
stdlib — never ``dsm.pii``/``dsm.ingest``/``modal``/``httpx`` (the PII boundary + cache are
injected at the CLI; the ``match ⊥ PII`` import contract holds).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import date, timedelta
from typing import Literal, Protocol, runtime_checkable

import dspy
import structlog
from pydantic import BaseModel

from dsm.config import load_prompt
from dsm.match.models import RoleIntake
from dsm.models import Location, OpenRole, SkillDepth, SkillRequirement

_log = structlog.get_logger("dsm.match.intake")

# Injected LLM seam: (prose, today) → RoleIntake (mocked in tests; live = PseudonymisedLM at CLI).
IntakePredictor = Callable[[str, date], RoleIntake]

_WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# The DSPy signature + predictor (single-shot; compileable)
# ---------------------------------------------------------------------------


class RoleIntakeSignature(dspy.Signature):
    """Parse a free-text staffing role request into a structured intake (config/prompts)."""

    request_text: str = dspy.InputField()
    today: str = dspy.InputField()  # injected ISO run-date — the A3 partial mitigation
    intake: RoleIntake = dspy.OutputField()


def _intake_module() -> dspy.Predict:
    """The bare DSPy module for intake — a plain ``Predict`` with config-loaded instructions.

    Kept a *bare* ``dspy.Predict`` with **no baked-in demos** so the signature can later be
    compiled offline against a golden set (MIPROv2, NF-6) — a compiler replaces the
    instruction/demos. Not ``ChainOfThought``/``ReAct``/``Refine``.
    """
    return dspy.Predict(RoleIntakeSignature.with_instructions(load_prompt("role_intake")))


def make_intake_predictor(lm: dspy.LM) -> IntakePredictor:
    """Build the real intake predictor over the injected LM (used by the CLI, not tests).

    The LM is ``PseudonymisedLM`` at the CLI, invoked **without** ``pii_context`` (pass-through —
    role text is non-PII, §7). Single-shot ``Predict`` at the LM's pinned ``temperature=0``.
    """
    predictor = _intake_module()

    def _predict(prose: str, today: date) -> RoleIntake:
        with dspy.context(lm=lm):
            return predictor(request_text=prose, today=today.isoformat()).intake

    return _predict


# ---------------------------------------------------------------------------
# Assembly result types
# ---------------------------------------------------------------------------


class ClarificationNeeded(BaseModel, frozen=True):
    """Required gate fields the prose did not yield — drives the one Python clarification round.

    ``missing`` is the bounded set of required gate fields (location / start) that could not
    be assembled; ``partial`` carries everything parsed so far so the CLI can re-assemble after
    the operator answers (FR-4). The LLM is never re-invoked on this path.
    """

    missing: list[Literal["location", "start"]]
    partial: RoleIntake


# ``assemble_role`` returns a ready OpenRole or a ClarificationNeeded (needs one bounded round).
RoleAssembly = OpenRole | ClarificationNeeded


# ---------------------------------------------------------------------------
# Pure assembly + deterministic validation (LLM-free)
# ---------------------------------------------------------------------------


def resolve_start_date(
    start_date_iso: str | None, today: date, *, max_horizon_days: int
) -> date | None:
    """Validate the LLM-resolved ISO start date deterministically (AD-XXY); ``None`` if unusable.

    The LLM resolves a (possibly relative) phrase to ``start_date_iso`` with today injected; this
    is the pure Python gate-side check that runs **before** the availability gate ever sees it:

    Args:
        start_date_iso: the LLM's ISO date string (``None``/empty when absent from the prose).
        today: the injected run-date — the floor of the plausibility window.
        max_horizon_days: the ceiling offset; a sanity bound (NOT a gate parameter, AD-XXY).

    Returns:
        The parsed ``date`` when it is a valid calendar date within ``[today, today +
        max_horizon_days]``; otherwise ``None`` (absent, malformed, or implausible → the caller
        surfaces ``start`` for clarification, FR-2). The value's determinism comes from
        ``temperature=0`` + the parse cache, not from this admissibility check.
    """
    if not start_date_iso:
        return None
    try:
        resolved = date.fromisoformat(start_date_iso.strip())
    except ValueError:
        _log.warning("intake.start_date_unparseable", value=start_date_iso)
        return None
    if resolved < today or resolved > today + timedelta(days=max_horizon_days):
        _log.warning(
            "intake.start_date_out_of_window",
            resolved=resolved.isoformat(),
            today=today.isoformat(),
            max_horizon_days=max_horizon_days,
        )
        return None
    return resolved


def _resolve_location(intake: RoleIntake) -> Location | None:
    """Build the role ``Location`` from the parsed intake, or ``None`` when it is missing.

    A named city → onsite-capable location (case preserved; the gate compares case-insensitively,
    AD-086). ``remote_within_country`` with no city → a remote role (valid; needs no city). Neither
    stated → ``None`` (the caller surfaces ``location`` for clarification; the LLM never guesses a
    city). ``onsite_cities`` is a candidate-side facet (AD-086) and is never populated from prose.
    """
    city = (intake.location_city or "").strip()
    if city:
        return Location(city=city, remote_within_country=intake.remote_within_country)
    if intake.remote_within_country:
        return Location(city=None, remote_within_country=True)
    return None


def _assemble_skills(intake: RoleIntake) -> list[SkillRequirement]:
    """Merge the two skill buckets into ``required_skills``, forcing ``depth`` from the bucket.

    Forcing the depth (every ``hard_skills`` element → HARD, every ``desired_skills`` → DESIRED)
    means the per-element ``depth`` the LLM emitted can never contradict the list it landed in
    (FR-1-AC-5). Skill ``name`` is normalised to lowercase (the index/score compare names exactly).
    """
    forced: list[SkillRequirement] = []
    for skill in intake.hard_skills:
        forced.append(
            skill.model_copy(update={"name": skill.name.strip().lower(), "depth": SkillDepth.HARD})
        )
    for skill in intake.desired_skills:
        forced.append(
            skill.model_copy(
                update={"name": skill.name.strip().lower(), "depth": SkillDepth.DESIRED}
            )
        )
    return forced


def assemble_role(
    intake: RoleIntake, today: date, *, max_horizon_days: int, role_id: str
) -> RoleAssembly:
    """Assemble + validate a ``RoleIntake`` into the existing ``OpenRole`` (or ask to clarify).

    Pure + LLM-free. Validates the start date (``resolve_start_date``), resolves the location,
    **derives** ``co_location_required`` in Python (FR-8 — never an LLM output), and forces skill
    depth. A missing required gate field (location / start) returns ``ClarificationNeeded`` so the
    CLI can run one bounded Python clarification round (FR-4) — the LLM is not re-invoked here.

    Args:
        intake: the LLM's parsed ``RoleIntake``.
        today: the injected run-date (date floor + relative-date reference).
        max_horizon_days: the start-date plausibility ceiling (``nl_intake.max_horizon_days``).
        role_id: the synthesized deterministic id (``"NL-<hash[:8]>"``) the caller supplies.

    Returns:
        A ready ``OpenRole`` when location + start both resolve, else ``ClarificationNeeded``
        naming the missing required field(s).
    """
    location = _resolve_location(intake)
    start_date = resolve_start_date(
        intake.start_date_iso, today, max_horizon_days=max_horizon_days
    )

    missing: list[Literal["location", "start"]] = []
    if location is None:
        missing.append("location")
    if start_date is None:
        missing.append("start")
    if missing:
        return ClarificationNeeded(missing=missing, partial=intake)

    assert location is not None and start_date is not None  # narrowed by the missing check above
    # FR-8 / AD-002: co_location_required is Python-derived, never taken from the LLM. A named city
    # implies onsite unless the request stated remote; gate inputs stay code-owned.
    co_location_required = bool(intake.location_city and intake.location_city.strip()) and not (
        intake.remote_within_country
    )
    role = OpenRole(
        role_id=role_id,
        title=intake.title or "",
        required_skills=_assemble_skills(intake),
        location=location,
        co_location_required=co_location_required,
        start_date=start_date,
        description=intake.notes,
    )
    _log.info(
        "intake.assembled",
        role_id=role_id,
        start_date=start_date.isoformat(),
        start_phrase=intake.start_date_phrase,
        co_location_required=co_location_required,
        hard_skills=len(intake.hard_skills),
        desired_skills=len(intake.desired_skills),
    )
    return role


# ---------------------------------------------------------------------------
# Content-addressed parse cache (AD-066 derivation version)
# ---------------------------------------------------------------------------


def intake_cache_key(prose: str, today: date, model_id: str, prompt_version: str) -> str:
    """The content-hash parse-cache key (FR-6) — ``sha256(prose | today | model_id | version)``.

    Pure. The prose is whitespace-normalised + lowercased so cosmetic variants share a cache entry.
    The run-date is a **deliberate** key term so a relative-date parse is never reused across days
    (FR-6-AC-2). ``(model_id, prompt_version)`` is the AD-066 derivation version — a bump in either
    changes the key and forces a re-parse, never silent reuse of a stale parse.
    """
    normalised = _WS.sub(" ", prose).strip().lower()
    payload = "\x1f".join([normalised, today.isoformat(), model_id, prompt_version])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@runtime_checkable
class IntakeCache(Protocol):
    """Port for the parse cache — ``get`` returns a cached ``RoleIntake`` or ``None`` (a miss)."""

    def get(self, key: str) -> RoleIntake | None:
        """Return the cached intake for ``key``, or ``None`` on a miss."""
        ...

    def put(self, key: str, value: RoleIntake) -> None:
        """Store ``value`` under ``key``."""
        ...


class NullIntakeCache:
    """A no-op cache — the default / pure-unit path (always a miss; stores nothing)."""

    def get(self, key: str) -> RoleIntake | None:
        return None

    def put(self, key: str, value: RoleIntake) -> None:
        return None
