"""Query-time text builders for the index layer (AD-091 split).

Write-time builders (``build_embed_text``, ``build_skill_set``, ``included_skills``) moved to
``dsm/index/build.py`` (the build edge). This module holds only **query-time** builders that
have no ``dsm.ingest`` dependency.
"""
