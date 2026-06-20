Extract structured signals from one anonymized feedback item about a consultant.

The text has had identities replaced with placeholder tokens (e.g. [[PII_0]], [[NER_0]]). Leave
those tokens exactly as they are.

Attach an EvidenceCitation whose `text` is a VERBATIM span copied exactly from the feedback. Do not
paraphrase or invent quotes — a citation whose quote is not found verbatim in the source is rejected.

Extract:
- confirmed_skills: skills the feedback explicitly confirms the consultant demonstrated.
- skill_gaps: skills the feedback says the consultant lacks, has not used, or did poorly.
- domain_confirmation: a domain the feedback confirms experience in, if any.
- sentiment: one of very_positive | positive | neutral | negative.
- retention_requested: true only if the client asks to keep / retain the consultant.
- rejection_requested: true only if the client asks not to staff / rejects the consultant.
- summary: one neutral sentence.

State only what the feedback supports. Do not emit any score — scoring happens downstream.
