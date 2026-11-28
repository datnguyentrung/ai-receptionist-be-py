"""Deterministic Tuition Calculator combining Java TrainingPlan prices with Layer 2 DiscountRules."""

from datetime import date

from app.schemas.knowledge.discount import (
    AppliedDiscountItem,
    DiscountRuleFormRequest,
    DiscountType,
    TuitionCalculationResult,
)


def calculate_tuition_deterministic(
    *,
    package_name: str,
    unit_price: float,
    months: int = 1,
    student_count: int = 1,
    discount_code: str = "",
    discount_rules: list[DiscountRuleFormRequest] | None = None,
    plan_id: str | None = None,
    today: date | None = None,
) -> TuitionCalculationResult:
    """Compute tuition deterministically from structured Java price + Layer 2 DiscountRules."""
    current_day = today or date.today()
    effective_months = max(1, months)
    effective_students = max(1, student_count)
    subtotal = round(unit_price * effective_months * effective_students, 2)

    normalized_code = discount_code.strip().casefold()
    eligible: list[DiscountRuleFormRequest] = []
    for rule in discount_rules or []:
        if rule.valid_from and current_day < rule.valid_from:
            continue
        if rule.valid_to and current_day > rule.valid_to:
            continue
        if effective_months < rule.min_months or effective_students < rule.min_students:
            continue
        if rule.applicable_plan_ids and plan_id:
            if str(plan_id) not in {str(pid) for pid in rule.applicable_plan_ids}:
                continue
        if rule.promo_code:
            if not normalized_code or rule.promo_code.strip().casefold() != normalized_code:
                continue
        eligible.append(rule)

    eligible.sort(key=lambda r: (r.priority, r.rule_code))
    remaining = subtotal
    applied: list[AppliedDiscountItem] = []

    for rule in eligible:
        if applied and not rule.stackable:
            continue
        if rule.discount_type == DiscountType.PERCENTAGE:
            saved = round(remaining * (rule.discount_value / 100.0), 2)
        else:
            saved = round(min(remaining, rule.discount_value), 2)
        if saved <= 0:
            continue
        remaining = round(max(0.0, remaining - saved), 2)
        applied.append(
            AppliedDiscountItem(
                rule_code=rule.rule_code,
                name=rule.name,
                discount_type=rule.discount_type,
                discount_value=rule.discount_value,
                amount_saved=saved,
            )
        )
        if not rule.stackable:
            break

    total_discount = round(subtotal - remaining, 2)
    return TuitionCalculationResult(
        package_name=package_name,
        plan_id=plan_id,
        months=effective_months,
        student_count=effective_students,
        unit_price=unit_price,
        subtotal=subtotal,
        total_discount=total_discount,
        final_total=remaining,
        applied_discounts=applied,
    )


__all__ = ["calculate_tuition_deterministic"]
