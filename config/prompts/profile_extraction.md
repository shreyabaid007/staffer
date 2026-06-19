Extract structured profile facts from an anonymized consultant resume.

The text has had identities replaced with placeholder tokens (e.g. [[PII_0]], [[NER_0]]). Leave
those tokens exactly as they are — do not guess what they stand for.

For every fact you extract, attach an EvidenceCitation whose `text` is a VERBATIM span copied
exactly from the resume (including any placeholder tokens). Do not paraphrase, summarise, or invent
quotes — a citation whose quote is not found verbatim in the source will be rejected.

Extract:
- skills: each with a surface-form name and, where the resume states it, a proficiency level
  (beginner | intermediate | advanced | expert).
- employers, projects, domains, seniority_signals (years, led delivery, scale), education.

Only state what the resume supports. Omit anything not evidenced. Do not emit any score or ranking.
