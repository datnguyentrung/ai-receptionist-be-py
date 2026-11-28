"""Pydantic form schemas for Class Suitability, Curriculum, and Enrollment Steps."""

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.knowledge.base import KnowledgeFormBase


class TargetGoal(StrEnum):
    FITNESS = "FITNESS"
    SELF_DEFENSE = "SELF_DEFENSE"
    COMPETITION = "COMPETITION"
    RECREATIONAL = "RECREATIONAL"
    DISCIPLINE = "DISCIPLINE"


class EnrollmentChannel(StrEnum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    HYBRID = "HYBRID"


class ClassSuitabilityFormRequest(KnowledgeFormBase):
    profile_code: str | None = Field(default=None, min_length=1, max_length=100)
    min_age: int = Field(ge=0, le=100)
    max_age: int | None = Field(default=None, ge=0, le=100)
    target_goals: list[TargetGoal | str] = Field(min_length=1)
    curriculum_summary: str | None = None
    curriculum_modules: list[dict[str, Any]] | list[str] = Field(default_factory=list)
    prerequisites: str | None = None
    course_id: UUID | None = None
    training_plan_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_age_range(self) -> "ClassSuitabilityFormRequest":
        if self.max_age is not None and self.min_age > self.max_age:
            raise ValueError("min_age must be <= max_age")
        return self


class ClassSuitabilityFormResponse(ClassSuitabilityFormRequest):
    entity_key: str
    scope_key: str = "admission_onboarding"


class EnrollmentStepFormRequest(KnowledgeFormBase):
    step_code: str | None = Field(default=None, min_length=1, max_length=100)
    step_order: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1)
    required_documents: list[str] = Field(default_factory=list)
    channel: EnrollmentChannel = EnrollmentChannel.HYBRID
    training_plan_ids: list[UUID] = Field(default_factory=list)


class EnrollmentStepFormResponse(EnrollmentStepFormRequest):
    entity_key: str
    scope_key: str = "admission_onboarding"
