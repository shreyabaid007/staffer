"""Exact hard-skill filter (step 5) + hybrid recall (step 6) — query-time retrieval (B-1/B-2).

Hard-skill filter (B-1): a candidate clears iff every hard skill name is in their ``skill_set``
and proficiency is at or above the floor. Adjacency is never consulted (AD-033/072).

Hybrid recall (B-2, AD-089): dense + BM25 + RRF fusion. **OFF by default** — passthrough wraps
every pool candidate as ``RetrievedCandidate`` with all scores None. When ON, embeds the role
query, runs dense + BM25 + RRF. ``EmbedError`` → fallback to passthrough + log warning.
"""

from __future__ import annotations

import structlog

from dsm.index.embed_client import EmbedClient, EmbedError
from dsm.index.models import RetrievedCandidate
from dsm.models import (
    Candidate,
    EligiblePool,
    Exclusion,
    ExclusionReason,
    ProficiencyLevel,
    Skill,
    SkillRequirement,
)

_log = structlog.get_logger("dsm.index.retrieve")

# Ordinal ranking for the proficiency floor (StrEnum has no inherent ordering).
_PROFICIENCY_ORDER: tuple[ProficiencyLevel, ...] = (
    ProficiencyLevel.BEGINNER,
    ProficiencyLevel.INTERMEDIATE,
    ProficiencyLevel.ADVANCED,
    ProficiencyLevel.EXPERT,
)
_PROFICIENCY_RANK = {level: index for index, level in enumerate(_PROFICIENCY_ORDER)}


def _best_proficiency_by_name(skills: list[Skill]) -> dict[str, ProficiencyLevel]:
    """Map each skill name to the candidate's highest proficiency for it (handles duplicates)."""
    best: dict[str, ProficiencyLevel] = {}
    for skill in skills:
        current = best.get(skill.name)
        if current is None or _PROFICIENCY_RANK[skill.proficiency] > _PROFICIENCY_RANK[current]:
            best[skill.name] = skill.proficiency
    return best


def _hard_skill_gap(candidate: Candidate, hard_skills: list[SkillRequirement]) -> str | None:
    """Return a human-readable gap detail if the candidate misses a hard skill, else ``None``."""
    held = _best_proficiency_by_name(candidate.skills)
    missing = sorted(req.name for req in hard_skills if req.name not in held)
    below = sorted(
        f"{req.name} ({held[req.name].value} < {req.min_proficiency.value})"
        for req in hard_skills
        if req.min_proficiency is not None
        and req.name in held
        and _PROFICIENCY_RANK[held[req.name]] < _PROFICIENCY_RANK[req.min_proficiency]
    )
    if not missing and not below:
        return None

    parts: list[str] = []
    if missing:
        parts.append("missing hard skills: " + ", ".join(missing))
    if below:
        parts.append("below proficiency floor: " + ", ".join(below))
    return "; ".join(parts)


def exact_hard_skill_filter(
    pool: EligiblePool,
    hard_skills: list[SkillRequirement],
) -> tuple[EligiblePool, list[Exclusion]]:
    """Filter an eligible pool to candidates clearing every hard skill exactly (FR-4).

    Args:
        pool: the post-gate ``EligiblePool`` to filter (may be empty).
        hard_skills: the role's hard requirements (``SkillDepth.HARD``); an empty list passes
            everyone (no hard requirement to clear).

    Returns:
        ``(filtered_pool, exclusions)`` — ``filtered_pool`` keeps the input's ``scorecard_id``
        and only candidates that hold every hard skill at/above its floor; ``exclusions`` carries
        one ``HARD_SKILL_MISMATCH`` record per dropped candidate, with the gap in ``detail``.
        Order is preserved; no adjacency is consulted (AD-033/072).
    """
    survivors: list[Candidate] = []
    exclusions: list[Exclusion] = []
    for candidate in pool.candidates:
        gap = _hard_skill_gap(candidate, hard_skills)
        if gap is None:
            survivors.append(candidate)
        else:
            exclusions.append(
                Exclusion(
                    candidate_email=candidate.email,
                    reason=ExclusionReason.HARD_SKILL_MISMATCH,
                    detail=gap,
                )
            )
    return EligiblePool(candidates=survivors, scorecard_id=pool.scorecard_id), exclusions


# ---------------------------------------------------------------------------
# Hybrid recall (B-2; AD-089)
# ---------------------------------------------------------------------------


def _rrf_score(dense_rank: int, bm25_rank: int, k: int = 60) -> float:
    """Reciprocal Rank Fusion score for a candidate appearing at given ranks."""
    return 1.0 / (k + dense_rank) + 1.0 / (k + bm25_rank)


def hybrid_recall(
    pool: EligiblePool,
    role_query: str,
    embed_client: EmbedClient | None = None,
    *,
    top_n: int = 100,
    enabled: bool = False,
) -> list[RetrievedCandidate]:
    """Hybrid dense + BM25 + RRF recall (AD-089).

    Args:
        pool: the post-gate, post-exact-filter eligible pool.
        role_query: the role query passage for embedding.
        embed_client: embedder/reranker client (required when enabled=True).
        top_n: maximum candidates to return when recall is enabled.
        enabled: when False (default), passthrough — all pool candidates wrapped as
            RetrievedCandidate with None scores. When True, runs dense + BM25 + RRF.

    Returns:
        List of RetrievedCandidate with provenance scores.
    """
    if not enabled or embed_client is None:
        return [RetrievedCandidate(candidate_id=c.email) for c in pool.candidates]

    try:
        candidate_texts = [
            c.profile_summary or " ".join(s.name for s in c.skills) for c in pool.candidates
        ]
        query_vec = embed_client.embed([role_query], mode="query")[0]
        passage_vecs = embed_client.embed(candidate_texts, mode="passage")

        dense_scores = [_dot(query_vec, pv) for pv in passage_vecs]
        bm25_scores = _simple_bm25(role_query, candidate_texts)

        dense_ranked = sorted(range(len(dense_scores)), key=lambda i: -dense_scores[i])
        bm25_ranked = sorted(range(len(bm25_scores)), key=lambda i: -bm25_scores[i])

        dense_rank_map = {idx: rank for rank, idx in enumerate(dense_ranked)}
        bm25_rank_map = {idx: rank for rank, idx in enumerate(bm25_ranked)}

        results: list[tuple[int, float, float, float]] = []
        for i in range(len(pool.candidates)):
            rrf = _rrf_score(dense_rank_map[i], bm25_rank_map[i])
            results.append((i, dense_scores[i], bm25_scores[i], rrf))

        results.sort(key=lambda t: -t[3])
        results = results[:top_n]

        return [
            RetrievedCandidate(
                candidate_id=pool.candidates[i].email,
                dense_score=ds,
                bm25_score=bs,
                rrf_score=rrf,
            )
            for i, ds, bs, rrf in results
        ]
    except EmbedError as exc:
        _log.warning("recall.embed_failed", reason=str(exc))
        return [RetrievedCandidate(candidate_id=c.email) for c in pool.candidates]


def _dot(a: list[float], b: list[float]) -> float:
    """Dot product of two vectors."""
    return sum(x * y for x, y in zip(a, b, strict=False))


def _simple_bm25(query: str, documents: list[str]) -> list[float]:
    """Simplified BM25 scoring for in-memory recall."""
    query_terms = set(query.lower().split())
    scores: list[float] = []
    for doc in documents:
        doc_terms = doc.lower().split()
        term_freq = sum(1 for t in doc_terms if t in query_terms)
        scores.append(term_freq / (len(doc_terms) + 1) if doc_terms else 0.0)
    return scores
