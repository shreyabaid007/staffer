# Product Steering — Demand–Supply Matcher

> Always-loaded context. High-signal, lean. The WHAT and WHY. Authoritative rationale for every rule below lives in `docs/decision.md`.

## Purpose
Replace a slow, inconsistent manual staffing process. Given **one open role**, produce a ranked, explainable shortlist of consultants and **surface the trade-offs** — a human still decides.

## User & job-to-be-done
A staffing manager at Parity Partners. The job: *"for this open role, who are my best options, why, and what am I trading off?"* Today it's done by hand in a spreadsheet, on judgment.

## Goal framing (read this twice)
We are **not** "removing human bias" — an LLM pipeline can introduce its own. We deliver **consistency + an auditable rationale**: same inputs → same shortlist, every recommendation traceable to real evidence. **Fairness is something we test for, not something we claim.**

## Product invariants (must always hold)
- Location and availability are **hard gates**; nothing ranks above a candidate who fails them, and an LLM cannot override them.
- A stated **hard/"depth" skill is never satisfied by an adjacent skill.**
- **Every claim in the output cites real source evidence.** No unsourced assertions.
- **Trade-offs are surfaced, never hidden.** Retention, unverified new joiners, uncertain roll-off dates appear as flags — they don't silently change the rank.
- When nothing fits: return **empty + reason + closest near-misses.** Never a forced or fabricated match.

## The rules of the system (current state)
- **Scope:** one role in → ranked **top-5** (config) out; **batch** over the current snapshot; **CLI** + a minimal single-page web review UI over the same spine (AD-XXX).
- **Candidate universe:** the people in the three supply sheets (Beach / Rolling Off / New Joiners); profiles + feedback enrich, joined by **email**.
- **Location gate (AD-086):** co-location required → candidate's home city matches the role city **or** the role city is in the candidate's `onsite_cities` set (working-remote alone does **not** clear an onsite gate); co-location not required → any same-country (India) location passes. _Supersedes the earlier "open-to-city" / `remote_eligible` framing._
- **Availability gate:** free by **role start + 14 days** ("free now" qualifies for any future start).
- **Score:** **~70% skill match + 30% feedback / track-record** (config).
- **Feedback:** internal EE **=** client (equal weight in score); **shown separately** in the rationale.
- **New joiners:** skills counted but flagged **`unverified`**.
- **Adjacency:** partial credit + flag; **never** clears a hard skill.
- **Explanation:** structured fields **+** a 1–2 sentence narrative per candidate.

## Out of scope (MVP)
Cultural-fit scoring · multi-role / team formation · real-time / streaming refresh · authenticated / multi-user web app · bulk upload · cross-role priority allocation · days-on-beach utilisation logic · a feedback **learning** loop (the web UI captures put-forward / set-aside decisions append-only, but they never feed ranking — AD-XXX).

## Definition of success
Pass the seed evals: **ROLE-01** (Aarav gated out on availability; Kotlin beach consultants + an `unverified` new joiner surface, all with a payments-domain gap) and **ROLE-02** (only Chennai-based / Chennai-open pass), plus negative cases. **100% pass = insufficient coverage.**
