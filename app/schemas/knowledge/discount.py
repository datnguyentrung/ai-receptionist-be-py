"""Pydantic form schemas for Discount Rules and deterministic Tuition Calculation."""

from datetime import date
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.knowledge.base import KnowledgeFormBase


class DiscountType(StrEnum):
    PERCENTAGE = "PERCENTAGE"
    FIXED_AMOUNT = "FIXED_AMOUNT"


class DiscountRuleFormRequest(KnowledgeFormBase):
    rule_code: str | None = Field(default=None, min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    discount_type: DiscountType
    discount_value: float = Field(gt=0)
    min_months: int = Field(default=1, ge=1)
    min_students: int = Field(default=1, ge=1)
    promo_code: str | None = Field(default=None, max_length=100)
    stackable: bool = False
    priority: int = Field(default=10, ge=0)
    applicable_plan_ids: list[UUID] = Field(default_factory=list)
    valid_from: date | None = None
    valid_to: date | None = None
    description: str | None = None
    branch_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_rule(self) -> "DiscountRuleFormRequest":
        if self.discount_type == DiscountType.PERCENTAGE and self.discount_value > 100:
            raise ValueError("Percentage discount_value cannot exceed 100")
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_from > self.valid_to
        ):
            raise ValueError("valid_from must be <= valid_to")
        return self


class DiscountRuleFormResponse(DiscountRuleFormRequest):
    entity_key: str
    scope_key: str = "tuition_and_discount"


class AppliedDiscountItem(BaseModel):
    rule_code: str
    name: str
    discount_type: DiscountType
    discount_value: float
    amount_saved: float


class TuitionCalculationResult(BaseModel):
    package_name: str
    plan_id: str | None = None
    months: int
    student_count: int
    unit_price: float
    subtotal: float
    total_discount: float
    final_total: float
    applied_discounts: list[AppliedDiscountItem] = Field(default_factory=list)
    notes: str | None = None
