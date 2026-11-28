"""Pydantic form schemas for Taekwondo Concepts, Poomsae, and Belt Ranks."""

from enum import StrEnum

from pydantic import Field

from app.schemas.knowledge.base import KnowledgeFormBase


class ConceptCategory(StrEnum):
    TECHNIQUE = "TECHNIQUE"
    RULE = "RULE"
    TERMINOLOGY = "TERMINOLOGY"
    BENEFIT = "BENEFIT"


class TargetAudience(StrEnum):
    KIDS = "KIDS"
    TEENS = "TEENS"
    ADULTS = "ADULTS"
    ALL = "ALL"


class TaekwondoConceptFormRequest(KnowledgeFormBase):
    name: str = Field(min_length=1, max_length=255)
    korean_name: str | None = Field(default=None, max_length=255)
    category: ConceptCategory
    target_audience: TargetAudience = TargetAudience.ALL
    description: str = Field(min_length=1)


class TaekwondoConceptFormResponse(TaekwondoConceptFormRequest):
    entity_key: str
    scope_key: str = "training_knowledge"


class PoomsaeFormRequest(KnowledgeFormBase):
    name: str = Field(min_length=1, max_length=255)
    korean_name: str | None = Field(default=None, max_length=255)
    movement_count: int | None = Field(default=None, ge=1)
    level: str | None = Field(default=None, max_length=100)
    description: str = Field(min_length=1)


class PoomsaeFormResponse(PoomsaeFormRequest):
    entity_key: str
    scope_key: str = "belt_and_exam"


class BeltRankFormRequest(KnowledgeFormBase):
    name: str = Field(min_length=1, max_length=255)
    rank_order: int = Field(ge=1)
    color: str = Field(min_length=1, max_length=100)
    meaning: str | None = None
    min_training_months: int | None = Field(default=None, ge=0)
    exam_requirements: list[str] = Field(default_factory=list)
    required_concepts: list[str] = Field(default_factory=list)
    required_poomsae: list[str] = Field(default_factory=list)
    next_rank_name: str | None = Field(default=None, max_length=255)


class BeltRankFormResponse(BeltRankFormRequest):
    entity_key: str
    scope_key: str = "belt_and_exam"
