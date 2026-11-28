"""Phase 4 verification suite for Layer 1 & Layer 2 Ontology, Schemas, FactMapper, Guards, and Tools."""

import uuid
from datetime import date

import pytest

from app.agent.tools import (
    admission_tools,
    center_info_tools,
    training_tools,
    tuition_tools,
)
from app.models import (
    OntologyEntityType,
    OntologyVersion,
    OntologyVersionStatus,
    SchemaProposalType,
)
from app.schemas.knowledge import (
    BeltRankFormRequest,
    CenterProfileFormRequest,
    ClassSuitabilityFormRequest,
    ConceptCategory,
    DiscountRuleFormRequest,
    DiscountType,
    EnrollmentStepFormRequest,
    FacilityCategory,
    FacilityItemFormRequest,
    PolicyCategory,
    PolicyRuleFormRequest,
    PoomsaeFormRequest,
    TaekwondoConceptFormRequest,
    TargetGoal,
)
from app.services.graphrag.retrieval import GraphQaRequest
from app.services.knowledge import (
    Layer2FactMapper,
    TenantKnowledgeStore,
    calculate_tuition_deterministic,
)
from app.services.ontology.ontology_lifecycle import (
    assert_proposal_respects_protected_layers,
    is_protected_entity,
)
from app.services.ontology.seeds import (
    LAYER_1_ENTITY_TYPES,
    LAYER_1_PROPERTIES,
    LAYER_2_ENTITY_TYPES,
    LAYER_2_PROPERTIES,
    LAYER_2_SCOPE_SPECS,
    TOOL_SCOPE_MAP,
)


def test_layer1_ownership_and_no_sensitive_entities():
    names = {e["technical_name"] for e in LAYER_1_ENTITY_TYPES}
    assert "branch" in names
    assert "training_plan" in names
    assert "course" in names
    # Sensitive/internal Java entities must never be exposed in Layer 1
    for forbidden in ("user", "auth_session", "wallet", "face_check_in_scan", "managed_device"):
        assert forbidden not in names

    for spec in LAYER_1_ENTITY_TYPES:
        meta = spec["metadata"]
        assert meta["layer"] == "LAYER_1_BUSINESS"
        assert meta["ownership"] == "BUSINESS_API"
        assert meta["sync_mode"] == "REFERENCE_ONLY"
        assert meta["source_binding"]["service"] == "be-java"


def test_layer2_does_not_duplicate_layer1_entities_or_operational_prices():
    l1_names = {e["technical_name"] for e in LAYER_1_ENTITY_TYPES}
    l2_names = {e["technical_name"] for e in LAYER_2_ENTITY_TYPES}
    assert l1_names.isdisjoint(l2_names)
    # Layer 2 must not duplicate Java's base_price/final_price/address/hotline
    assert "base_price" not in {p[0] for p in LAYER_2_PROPERTIES["discount_rule"]}
    assert "address" not in {p[0] for p in LAYER_2_PROPERTIES["center_profile"]}
    assert "hotline" not in {p[0] for p in LAYER_2_PROPERTIES["center_profile"]}
    assert "profile_code" not in {p[0] for p in LAYER_2_PROPERTIES["center_profile"]}


def test_pydantic_form_schemas_consistent_with_layer2_seed():
    schema_to_entity = {
        CenterProfileFormRequest: "center_profile",
        FacilityItemFormRequest: "facility_item",
        PolicyRuleFormRequest: "policy_rule",
        ClassSuitabilityFormRequest: "class_suitability_profile",
        EnrollmentStepFormRequest: "enrollment_step",
        TaekwondoConceptFormRequest: "taekwondo_concept",
        PoomsaeFormRequest: "poomsae",
        BeltRankFormRequest: "belt_rank",
        DiscountRuleFormRequest: "discount_rule",
    }
    for schema_cls, entity_name in schema_to_entity.items():
        seed_props = {p[0] for p in LAYER_2_PROPERTIES[entity_name]}
        model_fields = set(schema_cls.model_fields) - {
            "tenant_id",
            "branch_id",
            "branch_ids",
            "course_id",
            "training_plan_ids",
            "required_concepts",
            "required_poomsae",
            "next_rank_name",
        }
        assert model_fields.issubset(seed_props), (
            f"{schema_cls.__name__} fields {model_fields - seed_props} missing from Layer 2 seed"
        )


def test_fact_mapper_business_refs_and_tenant_isolation():
    mapper = Layer2FactMapper()
    plan_id = uuid.uuid4()
    course_id = uuid.uuid4()

    suitability = mapper.map_class_suitability(
        ClassSuitabilityFormRequest(
            tenant_id="tenant-van-quan",
            profile_code="kids-beginner",
            min_age=6,
            max_age=10,
            target_goals=[TargetGoal.FITNESS, TargetGoal.DISCIPLINE],
            curriculum_summary="Làm quen kỹ thuật căn bản và thái cực 1.",
            course_id=course_id,
            training_plan_ids=[plan_id],
        )
    )
    assert suitability.tenant_id == "tenant-van-quan"
    assert suitability.scope_key == "admission_onboarding"
    assert {(r.entity_type, r.business_id) for r in suitability.business_refs} == {
        ("course", str(course_id)),
        ("training_plan", str(plan_id)),
    }

    # Same form for a different tenant must produce a distinct isolated entity_key
    other_tenant = mapper.map_class_suitability(
        ClassSuitabilityFormRequest(
            tenant_id="tenant-ha-dong",
            profile_code="kids-beginner",
            min_age=6,
            max_age=10,
            target_goals=[TargetGoal.FITNESS],
        )
    )
    assert other_tenant.entity_key != suitability.entity_key


def test_fact_mapper_covers_all_layer2_forms():
    mapper = Layer2FactMapper()
    assert mapper.map_center_profile(
        CenterProfileFormRequest(tenant_id="t1", title="TKD Văn Quán", branch_ids=[1])
    ).scope_key == "center_overview"
    assert mapper.map_facility_item(
        FacilityItemFormRequest(tenant_id="t1", name="Thảm EVA", category=FacilityCategory.MAT, description="Thảm 4cm")
    ).scope_key == "center_overview"
    assert mapper.map_policy_rule(
        PolicyRuleFormRequest(tenant_id="t1", policy_code="trial-1", category=PolicyCategory.TRIAL, title="Học thử", summary="Miễn phí 1 buổi")
    ).scope_key == "admission_onboarding"
    assert mapper.map_enrollment_step(
        EnrollmentStepFormRequest(tenant_id="t1", step_code="step-1", step_order=1, title="Đăng ký", description="Điền form")
    ).scope_key == "admission_onboarding"
    assert mapper.map_taekwondo_concept(
        TaekwondoConceptFormRequest(tenant_id="t1", name="Ap Chagi", category=ConceptCategory.TECHNIQUE, description="Đá tống trước")
    ).scope_key == "training_knowledge"
    assert mapper.map_poomsae(
        PoomsaeFormRequest(tenant_id="t1", name="Taegeuk 1", description="Bài quyền số 1")
    ).scope_key == "belt_and_exam"
    assert mapper.map_belt_rank(
        BeltRankFormRequest(tenant_id="t1", name="Đai Trắng", rank_order=1, color="Trắng", next_rank_name="Đai Vàng")
    ).scope_key == "belt_and_exam"
    assert mapper.map_discount_rule(
        DiscountRuleFormRequest(
            tenant_id="t1",
            rule_code="EARLY6M",
            name="Đóng 6 tháng",
            discount_type=DiscountType.PERCENTAGE,
            discount_value=10,
            min_months=6,
        )
    ).scope_key == "tuition_and_discount"


def test_center_profile_is_a_tenant_singleton_without_profile_code():
    mapper = Layer2FactMapper()
    first = mapper.map_center_profile(
        CenterProfileFormRequest(tenant_id="tenant-van-quan", title="Taekwondo Văn Quán")
    )
    same_tenant = mapper.map_center_profile(
        CenterProfileFormRequest(tenant_id="tenant-van-quan", title="Tên mới")
    )
    other_tenant = mapper.map_center_profile(
        CenterProfileFormRequest(tenant_id="tenant-ha-dong", title="Taekwondo Hà Đông")
    )

    assert first.entity_key == same_tenant.entity_key
    assert first.entity_key != other_tenant.entity_key
    assert "profile_code" not in first.properties


def test_all_17_function_tools_have_valid_scope_and_are_wired():
    assert len(TOOL_SCOPE_MAP) == 17
    valid_scopes = set(LAYER_2_SCOPE_SPECS)
    for tool_name, scope_key in TOOL_SCOPE_MAP.items():
        assert scope_key in valid_scopes, f"{tool_name} -> unknown scope {scope_key}"

    tool_modules = (admission_tools, center_info_tools, training_tools, tuition_tools)
    for tool_name in TOOL_SCOPE_MAP:
        found = any(hasattr(mod, tool_name) for mod in tool_modules)
        assert found, f"Missing FunctionTool {tool_name}"


def test_layer1_and_layer2_protected_from_layer3_proposals():
    version_id = uuid.uuid4()
    l1_entity = OntologyEntityType(
        ontology_version_id=version_id,
        technical_name="training_plan",
        display_name="Training Plan",
        metadata_={"layer": "LAYER_1_BUSINESS", "ownership": "BUSINESS_API"},
    )
    l2_entity = OntologyEntityType(
        ontology_version_id=version_id,
        technical_name="policy_rule",
        display_name="Policy Rule",
        metadata_={"layer": "LAYER_2_CORE", "ownership": "CORE_KNOWLEDGE"},
    )
    assert is_protected_entity(l1_entity)
    assert is_protected_entity(l2_entity)

    with pytest.raises(ValueError, match="BUSINESS_API"):
        assert_proposal_respects_protected_layers(
            SchemaProposalType.NEW_PROPERTY,
            {"entityType": "training_plan", "technicalName": "hacked_field"},
            [l1_entity, l2_entity],
        )

    with pytest.raises(ValueError, match="CORE_KNOWLEDGE"):
        assert_proposal_respects_protected_layers(
            SchemaProposalType.NEW_ENTITY_TYPE,
            {"technicalName": "policy_rule"},
            [l1_entity, l2_entity],
        )


def test_deterministic_tuition_calculator():
    rules = [
        DiscountRuleFormRequest(
            tenant_id="t1",
            rule_code="LONG_6M",
            name="Ưu đãi 6 tháng",
            discount_type=DiscountType.PERCENTAGE,
            discount_value=10,
            min_months=6,
            stackable=True,
            priority=1,
        ),
        DiscountRuleFormRequest(
            tenant_id="t1",
            rule_code="SIBLING",
            name="Ưu đãi anh em",
            discount_type=DiscountType.FIXED_AMOUNT,
            discount_value=200000,
            min_students=2,
            stackable=True,
            priority=2,
        ),
    ]
    res = calculate_tuition_deterministic(
        package_name="Gói Cơ Bản",
        unit_price=500000,
        months=6,
        student_count=2,
        discount_rules=rules,
        today=date(2026, 11, 26),
    )
    # subtotal = 500,000 * 6 * 2 = 6,000,000
    # 10% off -> 5,400,000; minus 200,000 -> 5,200,000
    assert res.subtotal == 6000000.0
    assert res.total_discount == 800000.0
    assert res.final_total == 5200000.0
    assert [d.rule_code for d in res.applied_discounts] == ["LONG_6M", "SIBLING"]


def test_graph_qa_request_accepts_tenant_id_and_scope_key():
    req = GraphQaRequest(
        question="Chính sách học thử như thế nào?",
        scope_key="admission_onboarding",
        tenant_id="tenant-van-quan",
    )
    assert req.scope_key == "admission_onboarding"
    assert req.tenant_id == "tenant-van-quan"
    assert TenantKnowledgeStore is not None
