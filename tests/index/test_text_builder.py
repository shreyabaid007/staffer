"""Tests for dsm.index.text_builder.build_embed_text (a-005 T-002; IDX-2/PII-1; AC-2/AC-6)."""

from __future__ import annotations

import random
from datetime import date

from dsm.index.text_builder import (
    build_embed_text,
    build_role_query_passage,
    build_skill_set,
    included_skills,
)
from dsm.ingest.models import Confidence, GoldCandidate, Grade, MergedSkill, Sourced
from dsm.models import (
    FreeNow,
    Location,
    ProficiencyLevel,
    SkillDepth,
    SkillRequirement,
    TargetProfileScorecard,
)

# Sentinel identity values: if any leaks into embed_text, the assertions below catch it.
_SECRET_NAME = "Rajesh Kumar"
_SECRET_EMAIL = "rajesh.kumar@example.com"
_SECRET_CID = "cid:DEADBEEF"


def _gold(*, skills: list[MergedSkill], domains: list[str], projects: list[str]) -> GoldCandidate:
    return GoldCandidate(
        candidate_id=_SECRET_CID,
        name_vault_ref=_SECRET_NAME,  # deliberately a name-shaped value (PII probe)
        email_vault_ref=_SECRET_EMAIL,
        grade=Sourced(value=Grade.LEAD_CONSULTANT),
        location=Sourced(value=Location(city="Chennai")),
        availability=Sourced(value=FreeNow()),
        skills=skills,
        domains=[Sourced(value=d) for d in domains],
        projects=projects,
        gold_hash="sha256:g1",
        merge_version="merge-v1",
        prompt_version="enrich-v1",
        model_version="anthropic/claude-sonnet-4-6",
    )


def _skill(
    name: str, *, proficiency: ProficiencyLevel | None = None, demonstrated=None
) -> MergedSkill:
    return MergedSkill(
        name=name,
        proficiency=proficiency,
        demonstrated=demonstrated,
        confidence=Confidence.MEDIUM,
    )


class TestIncludedSkills:
    def test_excludes_only_demonstrated_false(self) -> None:
        gold = _gold(
            skills=[
                _skill("kotlin", demonstrated=True),
                _skill("react", demonstrated=None),
                _skill("terraform", demonstrated=False),
            ],
            domains=[],
            projects=[],
        )
        names = {s.name for s in included_skills(gold)}
        assert names == {"kotlin", "react"}


class TestBuildEmbedText:
    def test_composition_prefix_skills_projects(self) -> None:
        gold = _gold(
            skills=[_skill("kotlin", proficiency=ProficiencyLevel.EXPERT), _skill("react")],
            domains=["payments", "banking"],
            projects=["Led the card-auth rewrite."],
        )
        text = build_embed_text(gold)
        # domains sorted; skill phrases sorted by name; proficiency rendered when present
        assert (
            text == "Domains: banking, payments. kotlin expert, react. Led the card-auth rewrite."
        )

    def test_excludes_denied_skills(self) -> None:
        gold = _gold(
            skills=[_skill("kotlin"), _skill("terraform", demonstrated=False)],
            domains=[],
            projects=[],
        )
        text = build_embed_text(gold)
        assert "kotlin" in text
        assert "terraform" not in text

    def test_omits_empty_parts(self) -> None:
        gold = _gold(skills=[_skill("kotlin")], domains=[], projects=[])
        assert build_embed_text(gold) == "kotlin."

    def test_deterministic_byte_identical(self) -> None:
        def make() -> GoldCandidate:
            return _gold(
                skills=[_skill("react"), _skill("kotlin", proficiency=ProficiencyLevel.ADVANCED)],
                domains=["banking", "payments"],
                projects=["b project", "a project"],
            )

        assert build_embed_text(make()) == build_embed_text(make())

    def test_order_insensitive_to_input_skill_order(self) -> None:
        base = [
            _skill("kotlin", proficiency=ProficiencyLevel.EXPERT),
            _skill("react", proficiency=ProficiencyLevel.INTERMEDIATE),
            _skill("aws"),
            _skill("docker"),
        ]
        shuffled = base[:]
        random.Random(7).shuffle(shuffled)
        a = build_embed_text(_gold(skills=base, domains=["x"], projects=["p"]))
        b = build_embed_text(_gold(skills=shuffled, domains=["x"], projects=["p"]))
        assert a == b

    def test_pii_free_by_construction(self) -> None:
        """AC-6: gold with identity-shaped vault refs → no identity in the embedded passage."""
        gold = _gold(
            skills=[_skill("kotlin", proficiency=ProficiencyLevel.EXPERT)],
            domains=["payments"],
            projects=["Built the settlement pipeline."],
        )
        text = build_embed_text(gold)
        assert _SECRET_NAME not in text
        assert "Rajesh" not in text
        assert _SECRET_EMAIL not in text
        assert _SECRET_CID not in text
        assert "DEADBEEF" not in text


class TestBuildSkillSet:
    def test_excludes_denied_includes_true_and_none(self) -> None:
        """AC-1: demonstrated False excluded; True/None kept (AD-081)."""
        gold = _gold(
            skills=[
                _skill("kotlin", demonstrated=True),
                _skill("react", demonstrated=None),
                _skill("terraform", demonstrated=False),
            ],
            domains=[],
            projects=[],
        )
        assert build_skill_set(gold) == ["kotlin", "react"]

    def test_sorted_and_deduped(self) -> None:
        gold = _gold(
            skills=[_skill("react"), _skill("kotlin"), _skill("aws"), _skill("kotlin")],
            domains=[],
            projects=[],
        )
        assert build_skill_set(gold) == ["aws", "kotlin", "react"]

    def test_agrees_with_embed_text_on_denied(self) -> None:
        """The shared predicate keeps skill_set and embed_text consistent on a refuted skill."""
        gold = _gold(
            skills=[_skill("kotlin"), _skill("terraform", demonstrated=False)],
            domains=[],
            projects=[],
        )
        assert "terraform" not in build_skill_set(gold)
        assert "terraform" not in build_embed_text(gold)


def _scorecard(
    *,
    hard: list[SkillRequirement] | None = None,
    desired: list[SkillRequirement] | None = None,
    notes: str | None = None,
) -> TargetProfileScorecard:
    return TargetProfileScorecard(
        role_id="ROLE-01",
        hard_depth_skills=hard or [],
        desired_skills=desired or [],
        location=Location(city="Chennai"),
        co_location_required=True,
        start_date=date(2026, 7, 1),
        clarification_notes=notes,
    )


class TestBuildRoleQueryPassage:
    def test_skills_sorted_with_floor_inline_and_notes(self) -> None:
        passage = build_role_query_passage(
            _scorecard(
                hard=[
                    SkillRequirement(
                        name="kotlin",
                        depth=SkillDepth.HARD,
                        min_proficiency=ProficiencyLevel.EXPERT,
                    )
                ],
                desired=[SkillRequirement(name="aws", depth=SkillDepth.DESIRED)],
                notes="Must have led a payments platform.",
            )
        )
        # sorted by name (aws before kotlin); hard floor rendered inline; notes appended verbatim
        assert passage == "aws, kotlin expert. Must have led a payments platform."

    def test_omits_empty_parts(self) -> None:
        passage = build_role_query_passage(
            _scorecard(hard=[SkillRequirement(name="kotlin", depth=SkillDepth.HARD)])
        )
        assert passage == "kotlin."

    def test_deterministic_byte_identical(self) -> None:
        sc = _scorecard(
            hard=[
                SkillRequirement(
                    name="kotlin", depth=SkillDepth.HARD, min_proficiency=ProficiencyLevel.ADVANCED
                )
            ],
            desired=[SkillRequirement(name="react", depth=SkillDepth.DESIRED)],
        )
        assert build_role_query_passage(sc) == build_role_query_passage(sc)

    def test_skill_span_symmetric_with_candidate_passage(self) -> None:
        """FR-3-AC-2: role + candidate builders render the same skill span via _skill_phrase."""
        role = build_role_query_passage(
            _scorecard(
                hard=[
                    SkillRequirement(
                        name="kotlin",
                        depth=SkillDepth.HARD,
                        min_proficiency=ProficiencyLevel.EXPERT,
                    )
                ]
            )
        )
        candidate = build_embed_text(
            _gold(
                skills=[_skill("kotlin", proficiency=ProficiencyLevel.EXPERT)],
                domains=[],
                projects=[],
            )
        )
        # The shared _skill_phrase renders "kotlin expert" identically on both sides.
        assert "kotlin expert" in role
        assert "kotlin expert" in candidate
