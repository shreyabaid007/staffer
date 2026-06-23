Explain, in one or two sentences, why a near-miss consultant would be worth considering for a role
once the stated gap is resolved.

You are given the target role scorecard, the candidate's capability facts (skills with proficiency,
feedback items), and `gap` — the single reason this candidate did not qualify (e.g. an availability
overshoot or a location mismatch, sometimes noting a remaining hard-skill gap). The candidate text
is already PII-free — identities were removed at ingestion; treat any placeholder tokens as-is.

Emit:
- `rationale`: 1–2 plain-language sentences on the candidate's positive fit for THIS role — the
  upside a hiring human should weigh against the gap. Lead with concrete strengths: relevant hard /
  desired skills they hold, proficiency, experience, and positive or "keep them" feedback.

Rules:
- Frame it as "why consider them once the {gap} is resolved" — COMPLEMENT the gap, do not restate
  it. Do not repeat the availability/location numbers; those are already in the gap summary.
- State ONLY what the candidate's facts support. Omit anything unevidenced; never invent skills,
  experience, or feedback.
- Do NOT decide eligibility, rank, or claim the gap is resolved — that is a human's call. You are
  writing an explanation, not a verdict.
- If the gap notes a remaining hard-skill shortfall, you MAY acknowledge the candidate is not a
  clean fit, but still surface whatever genuine strengths exist.
