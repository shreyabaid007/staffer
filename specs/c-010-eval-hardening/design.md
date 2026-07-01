# c-010 — Design

## Modules touched / added
| Path | Change |
|---|---|
| `dsm/eval/guardrail_validation.py` | **new** — detector-as-classifier metrics (P/R/F1/TPR/TNR/κ) + threshold sweep + calibrated operating point. Pure. |
| `dsm/eval/red_team.py` | **new** — injection corpus types + `contains_injection` (offline stub detector + narrative-leak check) + ASR + report. Pure. |
| `dsm/eval/fairness.py` | **new** — counterfactual parity metrics + aggregate report. Pure. |
| `dsm/eval/hardening_fixtures.py` | **new** — signed-off-gated loaders for the injection + guardrail corpora (mirrors `golden_set.py`). |
| `tests/fixtures/injection_corpus.json` | **new** — signed-off prompt-injection payloads. |
| `tests/fixtures/guardrail_corpus.json` | **new** — signed-off labelled attack+benign text per detector category. |
| `tests/eval/test_guardrail_validation.py` | **new** — offline metric units + live detector validation (skip-gated on the extra). |
| `tests/eval/test_red_team.py` | **new** — offline metric units + guarded-pipeline ASR=0 + live susceptibility. |
| `tests/eval/test_fairness.py` | **new** — offline parity units + deterministic proxy-blindness + live sub-score parity. |
| `dsm/eval/README.md` · `docs/{tech,decision,backlog}.md` | **edit** — eval-suite table + Stack line + ADR + backlog ticks. |

No change to `dsm/models.py`, `dsm/match/*`, `dsm/guardrails/*`, `dsm/pii/*`, `config/default.yaml`,
or `pyproject.toml`. No `make check` scope change.

## Why this shape (mirrors the AI eval layer, AD-104/105/106)
The existing framework already has the right bones: a signed-off golden set + a `validate_judge`
TPR/TNR pattern + deterministic-vs-live tiers. c-010 **reuses** them:
- Detector validation mirrors `faithfulness.validate_judge` — a confusion matrix → metrics + an
  adoption bar; extended with κ (Verga et al. 2024: κ>0.6 substantial) + a threshold sweep.
- Corpora mirror the golden set — JSON + typed loader + `review_status: signed_off` gate.
- Each capability has a **deterministic offline tier** (always runs in `make eval`, hermetic) and a
  **live tier** (`eval_live`, skip-gated), exactly like faithfulness + live-smoke.

## Layering per capability
```
Guardrail validation │ offline: metric units (synthetic pairs)   │ live: real detectors → P/R/F1/κ + sweep (skip w/o extra)
Injection red-team    │ offline: ASR=0 through the GUARDED pipeline│ live: real LLM susceptibility (guard off, skip w/o keys)
Fairness parity       │ offline: deterministic layer proxy-blind   │ live: real LLM sub-score parity (skip w/o keys)
```
The deterministic offline tiers are the load-bearing regression guards; the live tiers are the
"real model" evidence and are cost/key-gated so a bare checkout stays green.

## Key design decisions
- **Detector "positive" = should-be-flagged.** Recall = attack catch-rate (a missed attack is the
  dangerous FN); precision guards over-blocking. Adoption: `recall ≥ 0.80` AND `precision ≥ 0.60`
  (the missed-attack floor is load-bearing; tune per category via fixtures).
- **Offline injection uses a deterministic stub detector** (`contains_injection` marker match) as
  the stand-in for the real `detect_jailbreak` model, so ASR=0 is provable hermetically; the real
  model's catch-rate is the *live* detector-validation tier. This is the same "stub offline / real
  live" split c-009 used.
- **Fairness offline is a proxy-blindness proof, not a bias test.** With a cassette the sub-scores
  are fixed, so the offline tier proves the *deterministic* layer ignores proxy text (a real
  regression guard); the *bias* question (does the LLM shift?) is the live tier. Documented honestly
  — no false assurance.
- **Cohen's κ added to detector reports** (and recommended for the faithfulness judge as a
  follow-up). The faithfulness judge is **self-family** (Claude judging Claude → self-preference
  bias per MT-Bench); a non-self-family judge / PoLL panel is a recommended follow-up, **not** done
  here (don't destabilise the already-adopted judge).

## Import boundary
`dsm/eval/{fairness,red_team,guardrail_validation}.py` import **nothing** from `dsm.match`,
`dsm.pii`, or `dsm.guardrails` (pure metric/data modules). `hardening_fixtures.py` imports only
`dsm.eval.red_team`. The *tests* wire the pipeline/guards. No new import-linter contract needed; the
existing contracts are unaffected.

## Testing strategy
| Test | Tier | Checks |
|------|------|--------|
| detector metric units (perfect/missed/overblock/κ/sweep) | `make eval` offline | FR-1-AC-1/2 |
| live detector validation (jailbreak/bias/toxicity) | `make eval` live (skip w/o extra) | FR-1-AC-1/3 |
| ASR metric units + guarded-pipeline ASR=0 + narrative-leak | `make eval` offline | FR-2-AC-1/2 |
| live injection susceptibility | `make eval` live (skip w/o keys) | FR-2-AC-3 |
| parity metric units + deterministic proxy-blindness | `make eval` offline | FR-3-AC-1 |
| live sub-score parity | `make eval` live (skip w/o keys) | FR-3-AC-2 |

## Decisions to ratify (AD-XXX placeholder)
- **AD-XXX · Production-grade AI eval hardening (guardrail validation · injection red-team ·
  fairness)** — Accepted — Add three additive `make eval` capabilities closing the 2026-best-practice
  gaps for a people-ranking RAG system: (1) validate the c-009 guardrail **detectors as classifiers**
  (P/R/F1/TPR/TNR/κ + threshold-sweep calibration), (2) a prompt-injection **red-team** with an
  attack-success-rate = 0 regression through the guarded pipeline (OWASP LLM01) + a live
  susceptibility probe, (3) a **counterfactual fairness** eval (deterministic proxy-blindness +
  live sub-score parity) backing the `product.md` promise. Pure `dsm/eval/` modules + signed-off
  corpora; deterministic offline tiers + skip-gated live tiers. **Deterministic core, frozen
  contract, gates, PII boundary, and the `make check` commit gate are all unchanged** — everything
  runs under `make eval`. Rejected/deferred: full RAGAS relevancy metrics (our output is a ranked
  list); a non-self-family / PoLL faithfulness judge (recommended follow-up); automated red-team
  tooling; group four-fifths reporting (needs real data); grounding-detector validation. See
  `specs/c-010-eval-hardening/`.
