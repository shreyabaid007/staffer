# Decision Log (ADRs) — Demand–Supply Matcher

> The repository's memory. **Read before proposing alternatives.** To change a decision, add a new entry that supersedes the old — never silently diverge.
> Format: **ID · Title** — *Status* — Decision — why / consequence.

## Architecture & method

- **AD-001 · Structured RAG, not agentic** — Accepted — LLM bounded to typed clarify + score steps; retrieval deterministic. Why: explainability, reproducibility, tractable evals, predictable cost.
- **AD-002 · Deterministic, LLM-free gates** — Accepted — Location + availability filtering is pure Python; an LLM cannot override eligibility. Why: trust boundary + fairness floor; fully unit-testable.
- **AD-003 · Spec-driven + steering files** — Accepted — Steering docs + per-feature specs are the source of truth; code derives from specs. Why: prevent drift, preserve context.

## PII & data

- **AD-010 · PII pseudonymisation is mandatory** — Accepted — All OpenRouter calls go through `PseudonymisedLM`; mapping in-memory only. Why: resumes are PII-dense; external LLM exposure is high-risk.
- **AD-011 · Embedder receives PII-free text** — Accepted — `name`/`email` excluded from embedding input by construction; Modal sees no identity. Why: keeps the boundary intact while hosting the embedder externally.
- **AD-012 · Email is the join key** — Accepted — Join xlsx ↔ profiles ↔ feedback on email; first names collide in the data.
- **AD-013 · Candidate universe = supply sheets** — Accepted — Candidates are the people in Beach / Rolling Off / New Joiners; profiles enrich. A profile with no supply row = staffed → not a candidate.

## Gating rules

- **AD-020 · Location gate** — Accepted — co-location=Yes → in-city or open-to-city (`Chennai-open` in this data); `remote-India` → any India location, city = soft preference. The co-location flag is the hard part.
- **AD-021 · Availability window = +14 days** — Accepted — Eligible if free-date ≤ role start + 14d; "free now" qualifies for any future start.
- **AD-022 · Roll-off confidence is a flag, not a gate** — Accepted — Gate on the stated date; surface low confidence as a "date may slip" trade-off.
- **AD-023 · Retention surfaces as a trade-off** — Accepted — Client "keep them" feedback is a flag/note; it never gates or silently down-ranks.

## Ranking & scoring

- **AD-030 · Score = 0.7 skill + 0.3 feedback** — Accepted — Deterministic Python combination of LLM sub-scores; weights in `config/`.
- **AD-031 · EE and client feedback weighted equally** — Accepted — Equal in the score; shown separately in the rationale.
- **AD-032 · New-joiner skills: counted, flagged `unverified`** — Accepted — No penalty; the human sees the uncertainty.
- **AD-033 · Adjacency: partial credit + flag, never clears a hard skill** — Accepted — Enforced in code; a `hard_depth_skill` needs an exact match.
- **AD-034 · "Willingness to learn" not modelled** — Accepted — Data doesn't capture it; out of v1.
- **AD-035 · Skill adjacency seed map** — Accepted — JVM (Kotlin↔Java) · Frontend (React↔Next.js, TS↔JS) · Cloud (AWS↔GCP) · Containers (Docker↔K8s) · SQL (Postgres↔MySQL↔SQL) · Data (Spark↔dbt↔Airflow) · Test (Selenium↔Cypress↔Playwright) · GenAI (LLM↔RAG↔vector) · ML (ML↔scikit-learn). Config-driven, expandable.

## Output & evaluation

- **AD-040 · Explanation = structured fields + narrative** — Accepted — Per candidate; every claim cites real evidence.
- **AD-041 · No-match path** — Accepted — Empty shortlist + reason + closest near-misses; never a forced match.
- **AD-042 · Eval = synthetic + invariants** — Accepted — No historical labels exist; seed cases ROLE-01 / ROLE-02 + negatives. 100% pass = insufficient coverage.
- **AD-043 · Top-5 shortlist default** — Accepted — Configurable.

## Scope & infra

- **AD-050 · MVP scope** — Accepted — Single role; batch over snapshot; CLI. Out: cultural fit, multi-role/team formation, streaming, web UI, cross-role priority allocation, days-on-beach utilisation.
- **AD-051 · Hosting & goal framing** — Accepted — BGE embedder on Modal (serverless GPU); NER local; reasoning LLM on OpenRouter. Goal = consistency + auditable rationale, **not** bias removal.
- **AD-052 · Open-weights LLM on Modal** — Deferred — Possible (data sovereignty, $1000 credits) but out of MVP; revisit if cost/sovereignty become binding.

## Foundation & contracts

- **AD-060 · Domain contracts FROZEN** — Accepted — The models in `dsm/models.py` are the single typed interface between all modules. Frozen after Slice 0 task F-003/F-004; changes require team agreement + a new superseding ADR. Why: parallel lane work (Data/Reasoning/Quality) breaks if the contract shifts mid-sprint. Consequence: `dsm/models.py` is treated as a published API — backwards-incompatible changes must go through an explicit decision.

## Process & workflow

- **AD-061 · Per-lane progress files + index refreshed at merge** — Accepted — Progress tracking is split: `docs/progress.md` is a thin **index describing `main`** (Current status, Works end-to-end, Active specs, Decisions); per-lane In flight / Next up / Blockers / Session log live in `docs/progress.{A,B,C}.md` (append-only, `merge=union` in `.gitattributes`). On a feature branch an engineer updates **only their own lane file** via `/handoff` (lane resolved from `.claude/lane`); the index is refreshed **only at merge to `main`, by whoever merges**, via `/handoff-index`. There is **no separate integrator role**. Why: three lanes (Data / Reasoning / Quality) can hand off in parallel without conflicting on one shared file, and the index stays a faithful snapshot of `main`. Consequence: supersedes the original single-shared-`progress.md` handoff model; pre-split session history is preserved as a frozen archive in the index.
- **AD-062 · Revised lane assignments** — Accepted — Supersedes **only the lane-assignment table** in AD-061 (the lane-file/handoff convention in AD-061 still stands). New assignments: **Lane A** (Data & Retrieval): `ingest/`, `index/`, `modal/`; **Lane B** (Reasoning): `match/clarify.py`, `match/score.py`; **Lane C** (Quality, PII & Interface): `match/gates.py`, `match/rank.py`, `pii/`, `cli/`, `eval/`, `Makefile`/CI. Why: gates and rank are the trust core — deterministic, fully unit-testable, tightly coupled with eval invariants and CLI output — so they belong with the quality/interface owner. See `docs/ownership.md` for the full slice plan.

## Gating & near-miss semantics

- **AD-063 · Gate semantics and near-miss assembly** — Accepted — Four sub-decisions:
  - **(a) Location gate semantics** — `candidate.location.remote_eligible` means "willing to work from a different location." Gate: `co_location_required=True` → pass if `candidate.location.city == scorecard.location.city` OR `candidate.location.remote_eligible is True`. **Refines AD-020's** "open-to-city" rule: the frozen model carries only a boolean, not per-city openness, so `remote_eligible` subsumes city-specific openness for MVP. `co_location_required=False` → any India location passes.
  - **(b) Near-miss ordering** — Cross-type: **availability misses rank above location misses** (availability near-misses are actionable — dates shift; location misses are structural). Within availability misses: smallest overshoot in days first. Within location misses: alphabetical by `candidate_email` (all location-miss candidates have `remote_eligible=False` by construction — G-LOC-2 passes anyone with `True` — so there is no meaningful gap metric to sort on).
  - **(c) Near-miss assembly** — The **orchestrator** (`dsm/cli/commands.py`) detects empty pool and builds `NoMatchResult.near_misses`. It recomputes gaps from the structured `Candidate` + `TargetProfileScorecard` objects it already holds — does NOT parse `Exclusion.detail`. `detail` stays human-readable only. Gates returns `(EligiblePool, ExclusionLog)` as typed. Rank returns `ShortlistResult` only — never `NoMatchResult`. Note: this supersedes `docs/structure.md` line 42 which previously assigned `NoMatchResult` to rank.
  - **(d) Top-3 near-misses** — `NoMatchResult.near_misses` is capped at 3 in the orchestrator. The model's list is unbounded; the cap is a presentation decision. Sufficient for "here's what came closest" without overwhelming the output.

---

*Next ADRs start at AD-064.*