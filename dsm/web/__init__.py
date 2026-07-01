"""Web frontend (c-008, AD-XXX) — a thin FastAPI JSON API + one static page over the spine.

A **composition root** (peer of ``dsm.cli``): it reuses the CLI builders + ``run_match`` /
``_run_role`` / ``render_identities`` and therefore inherits the PII boundary unchanged. It never
imports ``dsm.pii`` directly and adds no matching/scoring/eligibility logic. See
``specs/c-008-web-frontend/``.
"""
