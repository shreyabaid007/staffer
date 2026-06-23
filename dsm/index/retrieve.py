"""Exact hard-skill filter (step 5) — ``EligiblePool`` → filtered pool + exclusions (B-1; §6.5).

A stated **hard** skill is matched **structurally**, never by cosine adjacency (AD-033/072): a
candidate clears iff every hard-skill *name* is in their ``skill_set`` **and**, for each hard
skill carrying a ``min_proficiency`` floor, their matching skill's proficiency is at or above it
(ordinal ``ProficiencyLevel`` comparison, ``≥`` inclusive). Adjacency (AD-035) is **never**
consulted here — it contributes only to desired-skill coverage downstream (B-2).

Deterministic and LLM-free. Excluded candidates are recorded as
``Exclusion(reason=HARD_SKILL_MISMATCH, …)`` (AD-088) so the no-match path can explain the gap.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel

from dsm.index.embed_client import EmbedClient
from dsm.index.milvus_store import MilvusIndexStore
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

# RRF fusion constant (standard 60): rrf_score = Σ 1/(RRF_K + rank_i). Larger K flattens the
# contribution of top ranks; 60 is the de-facto default and keeps fusion deterministic.
_RRF_K = 60

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


class HardSkillGap(BaseModel, frozen=True):
    """A candidate's structured hard-skill shortfall vs a role (AD-098).

    Single source of truth for both ``exact_hard_skill_filter``'s exclusion detail and the no-match
    ``closest_on_skills`` builder, so the two can never drift (AD-072/033; no adjacency).
    """

    missing: list[str]  # hard skills absent by name
    below_floor: list[str]  # held but below the floor, e.g. "java (intermediate < expert)"

    @property
    def count(self) -> int:
        """Total shortfalls — ranks 'closest on skills' (fewest first, AD-098)."""
        return len(self.missing) + len(self.below_floor)


def hard_skill_gap(
    candidate: Candidate, hard_skills: list[SkillRequirement]
) -> HardSkillGap | None:
    """Structured hard-skill gap, or ``None`` if the candidate clears every hard skill.

    A gap is a hard skill whose *name* is absent from the candidate's skills, or a held skill whose
    proficiency is below the requirement's ``min_proficiency`` floor.
    """
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
    return HardSkillGap(missing=missing, below_floor=below)


def _hard_skill_gap(candidate: Candidate, hard_skills: list[SkillRequirement]) -> str | None:
    """Human-readable gap detail (``Exclusion.detail``) if the candidate misses a hard skill.

    Thin string view over ``hard_skill_gap`` — identical wording as before the AD-098 refactor.
    """
    gap = hard_skill_gap(candidate, hard_skills)
    if gap is None:
        return None
    parts: list[str] = []
    if gap.missing:
        parts.append("missing hard skills: " + ", ".join(gap.missing))
    if gap.below_floor:
        parts.append("below proficiency floor: " + ", ".join(gap.below_floor))
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
# Hybrid recall (step 6) — DEFERRED behind index.recall.enabled (B-2; §6.6/AD-089)
# ---------------------------------------------------------------------------


def _passthrough(candidates: list[Candidate]) -> list[RetrievedCandidate]:
    """The recall-OFF / fallback path: every candidate proceeds, all provenance scores ``None``.

    ``Candidate.email`` is the pseudonymised ``candidate_id`` (the Milvus PK / store key, AD-091),
    so it is the join back to both the store and the hydrated candidate downstream.
    """
    return [RetrievedCandidate(candidate_id=c.email) for c in candidates]


def hybrid_recall(
    candidates: list[Candidate],
    role_query: str,
    store: MilvusIndexStore,
    embed_client: EmbedClient,
    config: dict[str, Any],
) -> list[RetrievedCandidate]:
    """Dense ⊕ BM25 ⊕ RRF recall over the exact-filtered pool — OFF by default (AD-089; §6.6).

    Gated by ``config["index"]["recall"]["enabled"]`` (default ``False``). **OFF** → a passthrough:
    one ``RetrievedCandidate`` per input with all scores ``None`` (at single-digit gated pools
    recall has nothing to narrow — the gates + exact filter already did). **ON** → embed the role
    query (``mode="query"``), dense top-N ⊕ BM25 top-N (both restricted to the pool ids), fuse by
    RRF (deterministic: ``Σ 1/(RRF_K + rank)``), and return the fused top-N ordered by
    ``rrf_score`` then ``candidate_id`` asc. A store / embed error falls back to the exhaustive
    passthrough (never drops candidates, §6.6) — recall narrows for cost, not a correctness gate.

    Flipping ON is a config change, not a code change (AD-089): turn it on when the post-filter
    pool routinely exceeds ~150.
    """
    recall_cfg = config.get("index", {}).get("recall", {})
    if not bool(recall_cfg.get("enabled", False)):
        return _passthrough(candidates)

    top_n = int(recall_cfg.get("top_n", 100))
    ids = [c.email for c in candidates]  # email == candidate_id (AD-091)
    try:
        query_vector = embed_client.embed([role_query], mode="query")[0]
        dense = store.search_dense(query_vector, ids, top_n=top_n)
        bm25 = store.search_bm25(role_query, ids, top_n=top_n)
    except Exception as exc:  # noqa: BLE001 — EmbedError or any store error → exhaustive fallback
        _log.warning("recall.fallback_exhaustive", reason=type(exc).__name__)
        return _passthrough(candidates)

    dense_rank = {cid: rank for rank, (cid, _) in enumerate(dense, start=1)}
    bm25_rank = {cid: rank for rank, (cid, _) in enumerate(bm25, start=1)}
    dense_score = dict(dense)
    bm25_score = dict(bm25)

    fused: dict[str, float] = {}
    for cid in dense_rank.keys() | bm25_rank.keys():
        score = 0.0
        if cid in dense_rank:
            score += 1.0 / (_RRF_K + dense_rank[cid])
        if cid in bm25_rank:
            score += 1.0 / (_RRF_K + bm25_rank[cid])
        fused[cid] = score

    ordered = sorted(fused, key=lambda cid: (-fused[cid], cid))[:top_n]
    return [
        RetrievedCandidate(
            candidate_id=cid,
            dense_score=dense_score.get(cid),
            bm25_score=bm25_score.get(cid),
            rrf_score=fused[cid],
        )
        for cid in ordered
    ]


# ---------------------------------------------------------------------------
# Rerank (step 7) — cross-encoder precision stage (B-2; §6.7/AD-071)
# ---------------------------------------------------------------------------


def rerank(
    role_query: str,
    candidates: list[RetrievedCandidate],
    store: MilvusIndexStore,
    embed_client: EmbedClient,
    *,
    top_k: int,
) -> list[RetrievedCandidate]:
    """Cross-encoder rerank the recalled pool, truncated to ``top_k`` (§6.7/AD-071).

    Fetches each candidate's stored ``embed_text`` passage, scores every role–candidate pair
    jointly via ``EmbedClient.rerank`` (the Modal ``bge-reranker-base`` cross-encoder), sets
    ``rerank_score``, orders by it desc (``candidate_id`` asc tie-break, deterministic), and keeps
    the top ``top_k``. Truncation *narrows which candidates get LLM-scored* (step 8) — **not** the
    final shortlist order, which step 9 (``rank_assessments``) sets from ``combined_score``.

    Failure (``EmbedError`` or any store error) → return the pool **unranked**
    (``rerank_score=None``, **no** truncation) + log a warning; rerank is a precision lever, not an
    eligibility gate, so its absence degrades ordering only — step 9 still produces a total order.
    """
    if not candidates:
        return []

    ids = [c.candidate_id for c in candidates]
    try:
        texts = store.fetch_embed_texts(ids)
        passages = [texts.get(cid, "") for cid in ids]
        scores = embed_client.rerank(role_query, passages)
    except Exception as exc:  # noqa: BLE001 — EmbedError or store error → unranked passthrough
        _log.warning("rerank.unavailable", reason=type(exc).__name__)
        return candidates

    pairs = sorted(
        zip(candidates, scores, strict=True),
        key=lambda pair: (-float(pair[1]), pair[0].candidate_id),
    )
    ranked = [c.model_copy(update={"rerank_score": float(score)}) for c, score in pairs]
    return ranked[:top_k]
