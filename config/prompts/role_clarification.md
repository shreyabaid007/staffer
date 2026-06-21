Refine an open staffing role into a sharper target scorecard.

You are given the role's title, its free-text Notes / Constraints (`description`), and the
structured `required_skills` already parsed from the demand CSV. Your job is to interpret the
free text and produce a refined skill breakdown — nothing else.

Rules:
- You MAY add a skill the notes clearly require, strengthen a skill from desired to hard when the
  notes make it non-negotiable, or attach a minimum proficiency the notes state.
- You MAY capture constraints, context, or seniority intent in `clarification_notes` (1–3 short
  sentences).
- You MUST NOT invent a hard skill the notes do not support, and you MUST NOT relax or remove a
  hard requirement that was already parsed.
- You MUST NOT decide eligibility: location, co-location, start date, and the availability window
  are gates owned by deterministic code — never set, override, or comment on them as pass/fail.
- The notes describe the ROLE, not any person. There is no candidate identity here to protect.

Emit only the refined `hard_depth_skills`, `desired_skills`, and `clarification_notes`.
