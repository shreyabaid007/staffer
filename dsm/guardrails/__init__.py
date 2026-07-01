"""Guardrails AI defence-in-depth validation layer (c-009, AD-XXX).

A **complementary** input/output content-safety layer wrapped around the existing
``PseudonymisedLM`` call sites. It does **not** replace the PII boundary (AD-101/102), the
deterministic gates (AD-002), or the verbatim citation check (AD-073) — it addresses failure
modes those layers do not cover: prompt injection in untrusted candidate text, hallucinated
narrative prose, biased/toxic staffing language, and score-boundary violations.

**Composition-root concern, not a spine concern.** These guards are wired at ``dsm.cli.commands``
(mirroring the PII wiring); ``dsm.match`` never imports this package, and this package never
imports ``dsm.match`` / ``dsm.pii`` / ``dsm.ingest`` (enforced by ``tests/guardrails/test_imports``
+ an import-linter contract).

**Optional, eval-tier dependency.** ``guardrails-ai`` and its hub validators are an optional extra
(``pip install 'staffer[guardrails]'`` + ``guardrails hub install ...``). Every ``build_*`` helper
imports ``guardrails`` **lazily** and returns ``None`` when the master toggle / a validator flag
is off *or* the framework/validator is not installed, so importing this package never requires the
dependency and ``make check`` stays hermetic. The one always-on guard is the deterministic
score-bounds clamp (:func:`~dsm.guardrails.output_guard.validate_scores`), which is pure Python.
"""

from __future__ import annotations

from dsm.guardrails.input_guard import InputRejectedError

__all__ = ["InputRejectedError"]
