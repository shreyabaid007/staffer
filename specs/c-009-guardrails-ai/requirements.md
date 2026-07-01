# c-009 — Guardrails AI (input hardening + output validation)

## User story
As the system operator, I want defence-in-depth guardrails around every LLM call site so that
adversarial resume/feedback text cannot manipulate scoring, generated narratives are factually
grounded in source evidence, scores stay within valid bounds, and staffing narratives never contain
biased or toxic language — all without replacing the existing PII boundary.

## Scope & framing
Add the **Guardrails AI** framework (`guardrails-ai` + hub validators) as a composable input/output
validation layer around the existing `PseudonymisedLM` call sites. This is a **complementary**
layer — it does **not** replace the PII boundary (AD-101/102), the deterministic gates (`gates.py`),
the citation verification (`score.py`), or any existing eval invariant. It addresses failure modes
those layers do not cover:

- **FM-1 (P0):** Indirect prompt injection via resume/feedback text — no current defence.
- **FM-2 (P1):** Hallucinated narrative claims beyond verbatim citations — prose around quotes can
  fabricate experience levels, durations, or context the source doesn't support.
- **FM-3 (P1):** Biased or discriminatory language in generated staffing narratives — a liability
  for a staffing product.
- **FM-4 (P0):** Score boundary violations — LLM emitting sub-scores outside `[0.0, 1.0]`.

Three phases, shipped as one slice:
- **Phase 1 — Input hardening:** jailbreak + injection detection on untrusted text before it reaches
  the LLM.
- **Phase 2 — Score bounds + grounding:** validate sub-score ranges and check narrative sentences
  against source context.
- **Phase 3 — Bias + toxicity:** screen generated narratives and rationales for discriminatory or
  unprofessional language.

### Constraints carried over unchanged
- **Eligibility stays deterministic + LLM-free** (AD-002). Guardrails are validation-only — they
  never decide eligibility, gate a candidate, or alter a score (except clamping out-of-range).
- **PII boundary holds** (AD-101/102). The guardrails layer wraps around `PseudonymisedLM`, not
  inside it. Known-PII redaction + leak-scan remain the load-bearing guarantee.
- **Frozen contract untouched** (AD-060). No changes to `dsm/models.py`.
- **Deterministic gates untouched.** `dsm/match/gates.py` is never imported or modified.
- **Existing eval invariants untouched.** The six Tier-1 invariants remain the commit gate.

## Out of scope (this slice)
Topic restriction (`restricttotopic`) — prompts handle this adequately for the POC. Secrets
detection (`secrets_present`) — defence-in-depth but low priority. Gibberish text detection on
ingest input — deferred to a follow-up. Web-layer input validation (the `/intake` and `/match`
endpoints) — the guardrails apply at the LLM call site, covering both CLI and web paths. Custom
validator development — we use only off-the-shelf hub validators.

## Functional requirements (EARS)

### FR-1 — Input guard: prompt injection detection (Phase 1)
- **FR-1-AC-1:** WHEN resume text or feedback text is about to be sent to the LLM (at both call
  sites: ingest enrich and query-time score), the system SHALL validate the text against a jailbreak
  detection validator before the `PseudonymisedLM` call.
- **FR-1-AC-2:** WHEN the jailbreak validator detects an injection attempt, the system SHALL reject
  the text (raise an exception), log the rejection with the candidate_id (never the text content),
  and skip the LLM call for that candidate.
- **FR-1-AC-3:** WHEN a candidate is skipped due to injection detection at query time, the system
  SHALL surface this as an exclusion in the `ShortlistResult.exclusion_log` with reason
  `INPUT_REJECTED` (or similar), never silently drop the candidate.
- **FR-1-AC-4:** The input guard SHALL run **before** the PII redaction pass — adversarial text
  should be rejected before any processing resources are spent.

### FR-2 — Output guard: score bounds validation (Phase 2)
- **FR-2-AC-1:** WHEN the LLM returns `skill_match` or `feedback_score`, the system SHALL validate
  each is in `[0.0, 1.0]`.
- **FR-2-AC-2:** WHEN a score is outside `[0.0, 1.0]`, the system SHALL clamp it to the nearest
  bound (0.0 or 1.0), log the correction with the original value, and continue processing.
- **FR-2-AC-3:** The score validation SHALL be deterministic (no LLM in the loop) and add negligible
  latency.

### FR-3 — Output guard: narrative grounding (Phase 2)
- **FR-3-AC-1:** WHEN the LLM returns a narrative for a candidate, the system SHALL check each
  sentence against the candidate's source context (skills + feedback entries + profile summary) for
  factual support.
- **FR-3-AC-2:** WHEN a narrative sentence is not supported by the source context, the system SHALL
  either filter it out or flag it, and log the ungrounded sentence.
- **FR-3-AC-3:** The grounding check complements the existing verbatim citation verification
  (AD-073) — it covers **prose claims**, not quoted evidence text.
- **FR-3-AC-4:** The grounding check SHALL NOT block the response entirely — it filters or flags
  individual unsupported sentences while preserving grounded content.

### FR-4 — Output guard: bias + toxicity screening (Phase 3)
- **FR-4-AC-1:** WHEN the LLM returns a narrative or near-miss rationale, the system SHALL screen
  the text for age, gender, and ethnicity bias.
- **FR-4-AC-2:** WHEN bias is detected, the system SHALL trigger a re-generation (reask) of the
  narrative with the bias constraint surfaced to the LLM.
- **FR-4-AC-3:** WHEN the LLM returns a narrative or rationale, the system SHALL screen for toxic
  or unprofessional language.
- **FR-4-AC-4:** WHEN toxicity is detected, the system SHALL trigger a re-generation (reask).
- **FR-4-AC-5:** The bias and toxicity checks SHALL run on every human-facing text output: scoring
  narrative AND near-miss rationale.

### NF-1 — No spine modification
The guardrails layer SHALL be a wrapper around existing call sites. It SHALL NOT modify the scoring
logic, gate logic, ranking logic, or PII boundary. The `dsm/match` module SHALL NOT import
`dsm/guardrails` (guardrails are wired at the composition root, like PII).

### NF-2 — Configuration
All guardrails SHALL be independently toggleable via `config/default.yaml` under a `guardrails:`
section. Each validator SHALL have an `enabled: bool` flag. This allows selective deployment and
A/B comparison.

### NF-3 — Eval integration
- Guardrail rejections (injection, bias, toxicity) SHALL be observable in the eval harness as new
  Tier-1 invariant cases (e.g. planted injection text → candidate excluded, not ranked).
- LLM-based validators (grounding, bias, toxicity) SHALL NOT gate `make check` — they belong in
  `make eval` (Tier 2/3), like the faithfulness judge.
- Deterministic validators (score bounds) SHALL be testable with cassette data and belong in
  `make check`.

### NF-4 — Dependency
Add `guardrails-ai` to `pyproject.toml` under a new ADR (AD-XXX). Hub validators are installed via
`guardrails hub install` at setup time. The validators used:
- `guardrails/detect_jailbreak` (Phase 1)
- `guardrails/valid_range` (Phase 2)
- `bespokelabs/bespoke_minicheck` (Phase 2)
- `guardrails/bias_check` (Phase 3)
- `guardrails/toxic_language` (Phase 3)

### NF-5 — Latency awareness
Input guards (deterministic) add minimal latency. Output guards that use ML models (grounding,
bias, toxicity) SHOULD run in parallel where independent. The system SHALL log per-validator
latency so operators can tune which validators are enabled in production.

## Non-functional acceptance
- `make check` GREEN with the new validators enabled.
- New behaviour has tests (injection planted in fixture → candidate excluded; out-of-range score →
  clamped; biased narrative → reask triggered).
- New decisions in `docs/decision.md` (AD-XXX placeholder).
- `docs/progress.C.md` updated for the next session.
