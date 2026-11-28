"""Deterministic Form-to-Fact mapper for Layer 2 Core Knowledge (no LLM)."""

import hashlib
import json
from typing import Any

from app.schemas.knowledge import (
    POLICY_CATEGORY_SCOPE,
    BeltRankFormRequest,
    BusinessRef,
    CenterProfileFormRequest,
    ClassSuitabilityFormRequest,
    DiscountRuleFormRequest,
    EnrollmentStepFormRequest,
    FacilityItemFormRequest,
    KnowledgeFactRecord,
    KnowledgeRelationshipRecord,
    PolicyRuleFormRequest,
    PoomsaeFormRequest,
    TaekwondoConceptFormRequest,
)


def _stable_key(tenant_id: str, entity_type: str, identity: str) -> str:
    raw = json.dumps(
        {"tenantId": tenant_id, "entityType": entity_type, "id": identity.casefold()},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _branch_ref(branch_id: int) -> BusinessRef:
    return BusinessRef(entity_type="branch", business_id=str(branch_id))


def _plan_ref(plan_id: Any) -> BusinessRef:
    return BusinessRef(entity_type="training_plan", business_id=str(plan_id))


def _course_ref(course_id: Any) -> BusinessRef:
    return BusinessRef(entity_type="course", business_id=str(course_id))


class Layer2FactMapper:
    """Maps validated Admin Pydantic forms into Ontology-compliant KnowledgeFactRecords."""

    def map_center_profile(self, form: CenterProfileFormRequest) -> KnowledgeFactRecord:
        # One center profile exists per tenant.  The final component is an
        # implementation constant, not a user-managed profile code.
        key = _stable_key(form.tenant_id, "center_profile", "primary")
        refs = [_branch_ref(bid) for bid in form.branch_ids]
        rels = [
            KnowledgeRelationshipRecord(
                relationship_type="describes_branch",
                source_entity_type="center_profile",
                source_key=key,
                target_entity_type="branch",
                target_key=_stable_key(form.tenant_id, "branch", ref.business_id),
                target_business_ref=ref,
            )
            for ref in refs
        ]
        statements = [
            f"Trung tâm {form.title}: {form.history}" if form.history else f"Giới thiệu trung tâm: {form.title}",
        ]
        if form.vision or form.mission:
            statements.append(f"Tầm nhìn & Sứ mệnh ({form.title}): {form.vision or ''} {form.mission or ''}".strip())
        contacts = [x for x in (form.email, form.zalo_url, form.facebook_url, form.website_url) if x]
        if contacts:
            statements.append(f"Kênh liên hệ {form.title}: {', '.join(contacts)}")
        return KnowledgeFactRecord(
            tenant_id=form.tenant_id,
            scope_key="center_overview",
            entity_type="center_profile",
            entity_key=key,
            properties=form.model_dump(exclude={"tenant_id", "branch_ids"}, mode="json", exclude_none=True),
            business_refs=refs,
            relationships=rels,
            fact_statements=statements,
        )

    def map_facility_item(self, form: FacilityItemFormRequest) -> KnowledgeFactRecord:
        key = _stable_key(form.tenant_id, "facility_item", f"{form.category.value}:{form.name}")
        refs = [_branch_ref(form.branch_id)] if form.branch_id is not None else []
        rels = [
            KnowledgeRelationshipRecord(
                relationship_type="located_at_branch",
                source_entity_type="facility_item",
                source_key=key,
                target_entity_type="branch",
                target_key=_stable_key(form.tenant_id, "branch", ref.business_id),
                target_business_ref=ref,
            )
            for ref in refs
        ]
        return KnowledgeFactRecord(
            tenant_id=form.tenant_id,
            scope_key="center_overview",
            entity_type="facility_item",
            entity_key=key,
            properties=form.model_dump(exclude={"tenant_id", "branch_id"}, mode="json", exclude_none=True),
            business_refs=refs,
            relationships=rels,
            fact_statements=[f"Cơ sở vật chất ({form.category.value}) - {form.name}: {form.description}"],
        )

    def map_policy_rule(self, form: PolicyRuleFormRequest) -> KnowledgeFactRecord:
        scope_key = POLICY_CATEGORY_SCOPE[form.category]
        key = _stable_key(form.tenant_id, "policy_rule", form.policy_code)
        refs = [_branch_ref(bid) for bid in form.branch_ids]
        rels = [
            KnowledgeRelationshipRecord(
                relationship_type="applies_to_branch",
                source_entity_type="policy_rule",
                source_key=key,
                target_entity_type="branch",
                target_key=_stable_key(form.tenant_id, "branch", ref.business_id),
                target_business_ref=ref,
            )
            for ref in refs
        ]
        return KnowledgeFactRecord(
            tenant_id=form.tenant_id,
            scope_key=scope_key,
            entity_type="policy_rule",
            entity_key=key,
            properties=form.model_dump(exclude={"tenant_id", "branch_ids"}, mode="json", exclude_none=True),
            business_refs=refs,
            relationships=rels,
            fact_statements=[f"[{form.category.value}] {form.title}: {form.summary}"],
        )

    def map_class_suitability(self, form: ClassSuitabilityFormRequest) -> KnowledgeFactRecord:
        key = _stable_key(form.tenant_id, "class_suitability_profile", form.profile_code)
        refs: list[BusinessRef] = []
        rels: list[KnowledgeRelationshipRecord] = []
        if form.course_id is not None:
            cref = _course_ref(form.course_id)
            refs.append(cref)
            rels.append(
                KnowledgeRelationshipRecord(
                    relationship_type="profiles_course",
                    source_entity_type="class_suitability_profile",
                    source_key=key,
                    target_entity_type="course",
                    target_key=_stable_key(form.tenant_id, "course", cref.business_id),
                    target_business_ref=cref,
                )
            )
        for pid in form.training_plan_ids:
            pref = _plan_ref(pid)
            refs.append(pref)
            rels.append(
                KnowledgeRelationshipRecord(
                    relationship_type="profiles_plan",
                    source_entity_type="class_suitability_profile",
                    source_key=key,
                    target_entity_type="training_plan",
                    target_key=_stable_key(form.tenant_id, "training_plan", pref.business_id),
                    target_business_ref=pref,
                )
            )
        goals = ", ".join(g.value if hasattr(g, "value") else str(g) for g in form.target_goals)
        age_text = (
            f"độ tuổi {form.min_age}-{form.max_age}"
            if form.max_age is not None
            else f"từ {form.min_age} tuổi trở lên"
        )
        statements = [
            f"Hồ sơ lớp {form.profile_code}: {age_text}, mục tiêu {goals}.",
        ]
        if form.curriculum_summary:
            statements.append(f"Giáo trình {form.profile_code}: {form.curriculum_summary}")
        return KnowledgeFactRecord(
            tenant_id=form.tenant_id,
            scope_key="admission_onboarding",
            entity_type="class_suitability_profile",
            entity_key=key,
            properties=form.model_dump(
                exclude={"tenant_id", "course_id", "training_plan_ids"},
                mode="json",
                exclude_none=True,
            ),
            business_refs=refs,
            relationships=rels,
            fact_statements=statements,
        )

    def map_enrollment_step(self, form: EnrollmentStepFormRequest) -> KnowledgeFactRecord:
        key = _stable_key(form.tenant_id, "enrollment_step", form.step_code)
        refs = [_plan_ref(pid) for pid in form.training_plan_ids]
        rels = [
            KnowledgeRelationshipRecord(
                relationship_type="applies_to_plan",
                source_entity_type="enrollment_step",
                source_key=key,
                target_entity_type="training_plan",
                target_key=_stable_key(form.tenant_id, "training_plan", ref.business_id),
                target_business_ref=ref,
            )
            for ref in refs
        ]
        return KnowledgeFactRecord(
            tenant_id=form.tenant_id,
            scope_key="admission_onboarding",
            entity_type="enrollment_step",
            entity_key=key,
            properties=form.model_dump(exclude={"tenant_id", "training_plan_ids"}, mode="json", exclude_none=True),
            business_refs=refs,
            relationships=rels,
            fact_statements=[f"Bước {form.step_order} ({form.title}): {form.description}"],
        )

    def map_taekwondo_concept(self, form: TaekwondoConceptFormRequest) -> KnowledgeFactRecord:
        key = _stable_key(form.tenant_id, "taekwondo_concept", f"{form.category.value}:{form.name}")
        return KnowledgeFactRecord(
            tenant_id=form.tenant_id,
            scope_key="training_knowledge",
            entity_type="taekwondo_concept",
            entity_key=key,
            properties=form.model_dump(exclude={"tenant_id"}, mode="json", exclude_none=True),
            fact_statements=[f"[{form.category.value}] {form.name}: {form.description}"],
        )

    def map_poomsae(self, form: PoomsaeFormRequest) -> KnowledgeFactRecord:
        key = _stable_key(form.tenant_id, "poomsae", form.name)
        return KnowledgeFactRecord(
            tenant_id=form.tenant_id,
            scope_key="belt_and_exam",
            entity_type="poomsae",
            entity_key=key,
            properties=form.model_dump(exclude={"tenant_id"}, mode="json", exclude_none=True),
            fact_statements=[f"Bài quyền {form.name}: {form.description}"],
        )

    def map_belt_rank(self, form: BeltRankFormRequest) -> KnowledgeFactRecord:
        key = _stable_key(form.tenant_id, "belt_rank", form.name)
        rels: list[KnowledgeRelationshipRecord] = []
        for concept in form.required_concepts:
            rels.append(
                KnowledgeRelationshipRecord(
                    relationship_type="requires_concept",
                    source_entity_type="belt_rank",
                    source_key=key,
                    target_entity_type="taekwondo_concept",
                    target_key=_stable_key(form.tenant_id, "taekwondo_concept", f"TECHNIQUE:{concept}"),
                )
            )
        for poomsae_name in form.required_poomsae:
            rels.append(
                KnowledgeRelationshipRecord(
                    relationship_type="requires_poomsae",
                    source_entity_type="belt_rank",
                    source_key=key,
                    target_entity_type="poomsae",
                    target_key=_stable_key(form.tenant_id, "poomsae", poomsae_name),
                )
            )
        if form.next_rank_name:
            rels.append(
                KnowledgeRelationshipRecord(
                    relationship_type="next_rank",
                    source_entity_type="belt_rank",
                    source_key=key,
                    target_entity_type="belt_rank",
                    target_key=_stable_key(form.tenant_id, "belt_rank", form.next_rank_name),
                )
            )
        return KnowledgeFactRecord(
            tenant_id=form.tenant_id,
            scope_key="belt_and_exam",
            entity_type="belt_rank",
            entity_key=key,
            properties=form.model_dump(
                exclude={"tenant_id", "required_concepts", "required_poomsae", "next_rank_name"},
                mode="json",
                exclude_none=True,
            ),
            relationships=rels,
            fact_statements=[
                f"Cấp đai #{form.rank_order} {form.name} (màu {form.color}): {form.meaning or ''}".strip()
            ],
        )

    def map_discount_rule(self, form: DiscountRuleFormRequest) -> KnowledgeFactRecord:
        key = _stable_key(form.tenant_id, "discount_rule", form.rule_code)
        refs = [_plan_ref(pid) for pid in form.applicable_plan_ids] + [
            _branch_ref(bid) for bid in form.branch_ids
        ]
        rels = [
            KnowledgeRelationshipRecord(
                relationship_type="applies_to_plan" if ref.entity_type == "training_plan" else "applies_to_branch",
                source_entity_type="discount_rule",
                source_key=key,
                target_entity_type=ref.entity_type,
                target_key=_stable_key(form.tenant_id, ref.entity_type, ref.business_id),
                target_business_ref=ref,
            )
            for ref in refs
        ]
        return KnowledgeFactRecord(
            tenant_id=form.tenant_id,
            scope_key="tuition_and_discount",
            entity_type="discount_rule",
            entity_key=key,
            properties=form.model_dump(
                exclude={"tenant_id", "branch_ids"},
                mode="json",
                exclude_none=True,
            ),
            business_refs=refs,
            relationships=rels,
            fact_statements=[
                f"Ưu đãi {form.name} ({form.rule_code}): giảm {form.discount_value} ({form.discount_type.value}), tối thiểu {form.min_months} tháng, {form.min_students} học viên."
            ],
        )

    def to_candidate_facts(
        self, record: KnowledgeFactRecord, *, form_type: str
    ) -> list[Any]:
        """Convert a mapped KnowledgeFactRecord into structured CandidateFacts with explicit predicates."""
        from app.services.knowledge.governance_pipeline import CandidateFact

        primary_predicate_by_entity = {
            "center_profile": "history",
            "facility_item": "description",
            "policy_rule": "summary",
            "class_suitability_profile": "curriculum_summary",
            "enrollment_step": "description",
            "taekwondo_concept": "description",
            "poomsae": "description",
            "belt_rank": "exam_requirements",
            "discount_rule": "discount_value",
        }
        predicate = primary_predicate_by_entity.get(record.entity_type, "summary")
        facts: list[CandidateFact] = []
        for idx, stmt in enumerate(record.fact_statements):
            facts.append(
                CandidateFact(
                    fact_id=f"{record.tenant_id}:{form_type}:{record.entity_key}:{idx}",
                    entity_type=record.entity_type,
                    property_name=predicate,
                    predicate=predicate,
                    fact_type="PROPERTY",
                    subject_key=record.entity_key,
                    source_type="FORM",
                    form_type=form_type,
                    statement=stmt,
                    scope_key=record.scope_key,
                )
            )
        return facts


__all__ = ["Layer2FactMapper"]
