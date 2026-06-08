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

---
*Next ADRs start at AD-060. New decisions taken during implementation are appended here, not scattered in code or commit messages.*
