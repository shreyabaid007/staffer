# c-010 — Production-grade AI eval hardening

## User story
As the system operator, I want our evaluation framework to meet 2026 enterprise LLM/RAG eval
standards — validated guardrail detectors, an injection red-team regression, and a fairness
counterfactual eval — so that before we point this people-ranking system at real data we have
measurable evidence (not claims) that the guardrails work, adversarial profiles can't manipulate
ranking, and demographic proxies don't move scores. All **additive** to `make eval`; the
deterministic commit gate (`make check`) and the deterministic spine are untouched.

## Scope & framing
Against 2026 best practice (RAG triad / RAGAS; LLM-judge validation incl. Cohen's κ + judge-bias
mitigations; OWASP LLM Top-10 2025 red-teaming with an attack-success-rate regression; guardrail
detectors validated as classifiers; counterfactual fairness + the four-fifths rule; threshold
calibration via F1/Youden's J), our framework already covers: deterministic Tier-1 invariants,
cassette regression, live smoke, a **validated** faithfulness judge (TPR/TNR ≥ 0.80, AD-105), and
deterministic retrieval metrics (AD-106). This slice closes the three highest-value gaps for a
system that ranks people:

- **FM-A — guardrail detectors unvalidated:** the c-009 guards are wired + stub-tested, but the
  ML detectors themselves (jailbreak/bias/toxicity) were never measured on a labelled corpus.
- **FM-B — no injection red-team:** poisoned `gold.projects`/feedback text reaches the score LLM;
  there is no eval asserting an attack can't reach/manipulate scoring (OWASP LLM01).
- **FM-C — no fairness test:** `product.md` promises "fairness is something we test for" — no such
  test exists for a people-ranking system.

### Constraints carried over unchanged
- Deterministic spine + `dsm/models.py` (AD-060) + gates (AD-002) untouched. New code lives only in
  `dsm/eval/` + `tests/eval/` + `tests/fixtures/`.
- `make check` (the commit gate) is **not** extended — all new tiers run under `make eval`.
- New eval modules stay pure (no `dsm.match`/`dsm.pii`/`dsm.guardrails` import at module load);
  tests wire the pipeline/guards. Labelled corpora are **signed-off-gated** like the golden set.

## Out of scope (this slice)
Full RAGAS answer/context-relevancy metrics (our "answer" is a structured ranked list, already
covered by rank/gate invariants + the faithfulness judge). A multi-judge panel / non-self-family
faithfulness judge (recommended follow-up — the current judge is self-family). Automated red-team
tooling (garak/PyRIT/DeepTeam) — a curated corpus + ASR suffices for the POC. Group-level
four-fifths adverse-impact reporting over a real population (needs real data; the counterfactual
is the pre-data guard). Online/production monitoring + drift. Grounding-detector validation (needs
paired context — deferred).

## Functional requirements (EARS)

### FR-1 — Guardrail-detector validation + calibration (FM-A)
- **FR-1-AC-1:** The system SHALL score each guardrail detector (jailbreak / bias / toxicity) as a
  binary classifier over a labelled attack+benign corpus, reporting precision, recall, F1, TPR,
  TNR, and Cohen's κ.
- **FR-1-AC-2:** The system SHALL provide a threshold sweep + a calibrated operating point (F1- or
  Youden's-J-optimal) for score-based detectors.
- **FR-1-AC-3:** The live detector validation SHALL run only when `guardrails-ai` + the relevant
  hub validators are installed, and SHALL skip cleanly otherwise (never red on a bare checkout).

### FR-2 — Prompt-injection red-team (FM-B, OWASP LLM01)
- **FR-2-AC-1:** WHEN each payload in a signed-off injection corpus is planted into a candidate's
  profile/feedback and run through the **guarded** pipeline, the system SHALL reject it before the
  LLM call so the candidate is never ranked (attack-success-rate = 0).
- **FR-2-AC-2:** The system SHALL assert no surviving narrative echoes an injected instruction.
- **FR-2-AC-3:** A live tier SHALL quantify the **residual** susceptibility of the real score LLM
  (guard off) and skip without keys.

### FR-3 — Counterfactual fairness parity (FM-C)
- **FR-3-AC-1:** The system SHALL prove the **deterministic** layer (gates + coverages + combine +
  rank) is invariant under a demographic-proxy swap (offline, always runs).
- **FR-3-AC-2:** A live tier SHALL assert the **real** score LLM's sub-scores stay within a
  tolerance under the same proxy swap, and skip without keys.

### NF-1 — Reporting
Each validation SHALL emit a typed report (precision/recall/F1/κ; ASR; parity max-delta) surfaced
via `warnings`/logging — non-gating, mirroring the faithfulness-judge validation report (AD-105).

### NF-2 — Governance framing
The eval README SHALL map the suite to NIST AI RMF (Govern/Map/Measure/Manage) so the coverage is
auditable.

## Non-functional acceptance
- `make check` GREEN and **unchanged in scope** (still ignores `tests/eval`; commit gate intact).
- `make eval` GREEN: new offline tiers pass; live tiers skip cleanly without keys/the extra.
- New decisions in `docs/decision.md` (AD-XXX placeholder).
- `docs/progress.C.md` updated.
