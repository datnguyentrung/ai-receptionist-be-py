"""Pydantic form schemas for Policy Rules across Layer 2 scopes."""

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from app.schemas.knowledge.base import KnowledgeFormBase


class PolicyCategory(StrEnum):
    TRIAL = "TRIAL"
    OPENING_HOURS = "OPENING_HOURS"
    TRAINING_CONDUCT = "TRAINING_CONDUCT"
    LEAVE_MAKEUP = "LEAVE_MAKEUP"
    BELT_EXAM = "BELT_EXAM"
    TUITION = "TUITION"
    PAYMENT = "PAYMENT"


POLICY_CATEGORY_SCOPE: dict[PolicyCategory, str] = {
    PolicyCategory.TRIAL: "admission_onboarding",
    PolicyCategory.OPENING_HOURS: "center_overview",
    PolicyCategory.TRAINING_CONDUCT: "training_knowledge",
    PolicyCategory.LEAVE_MAKEUP: "training_knowledge",
    PolicyCategory.BELT_EXAM: "belt_and_exam",
    PolicyCategory.TUITION: "tuition_and_discount",
    PolicyCategory.PAYMENT: "tuition_and_discount",
}


class PolicyRuleFormRequest(KnowledgeFormBase):
    policy_code: str | None = Field(default=None, min_length=1, max_length=100)
    category: PolicyCategory
    title: str = Field(min_length=1, max_length=255)
    summary: str = Field(min_length=1)
    conditions: list[str] | dict[str, Any] = Field(default_factory=list)
    operating_hours: dict[str, Any] | None = None
    payment_methods: list[dict[str, Any]] | dict[str, Any] | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    branch_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dates(self) -> "PolicyRuleFormRequest":
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_from > self.effective_to
        ):
            raise ValueError("effective_from must be <= effective_to")
        return self


class PolicyRuleFormResponse(PolicyRuleFormRequest):
    entity_key: str
    scope_key: str
