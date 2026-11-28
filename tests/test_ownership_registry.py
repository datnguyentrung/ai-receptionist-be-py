"""Tests for Step 1: 3-Layer Data Ownership Registry, schema/predicate-level ownership, and Layer 3 guards."""

import pytest

from app.core.tenant_context import TrustedTenantContext
from app.schemas.knowledge import PolicyCategory, PolicyRuleFormRequest
from app.services.knowledge.fact_mapper import Layer2FactMapper
from app.services.knowledge.governance_pipeline import (
    CandidateFact,
    GateSeverity,
    KnowledgeGovernancePipeline,
)
from app.services.knowledge.workflow_service import (
    KnowledgeItemStatus,
    KnowledgeWorkflowService,
)
from app.services.ontology.ownership_registry import (
    ExtensionMode,
    FactType,
    IngestionMode,
    OwnershipRegistry,
    OwnershipVerdict,
    SourceOfTruth,
    VisibilityLevel,
)
from app.services.ontology.seeds import (
    LAYER_1_ENTITY_TYPES,
    LAYER_1_PROPERTIES,
    LAYER_2_PROPERTIES,
)


def test_layer1_property_and_relationship_governance_levels():
    registry = OwnershipRegistry.from_seeds()

    # Entity-level: REFERENCE_ONLY + LOCKED
    branch_gov = registry.resolve_entity("branch")
    assert branch_gov is not None
    assert branch_gov.source_of_truth == SourceOfTruth.BUSINESS_API
    assert branch_gov.ingestion_mode == IngestionMode.REFERENCE_ONLY
    assert branch_gov.extension_mode == ExtensionMode.LOCKED
    assert branch_gov.visibility == VisibilityLevel.PUBLIC
    assert branch_gov.review_required is False
    assert branch_gov.source_binding is not None
    assert "center_info" in branch_gov.owner_agent_keys

    # ID property: REFERENCE_ONLY; operational property: DENY
    branch_id_gov = registry.resolve_property("branch", "branch_id")
    assert branch_id_gov is not None
    assert branch_id_gov.ingestion_mode == IngestionMode.REFERENCE_ONLY

    for entity_name, prop_name in (
        ("branch", "address"),
        ("branch", "hotline"),
        ("class_schedule", "start_time"),
        ("class_schedule", "end_time"),
        ("course", "capacity"),
        ("training_plan", "base_price"),
        ("training_plan", "final_price"),
    ):
        gov = registry.resolve_property(entity_name, prop_name)
        assert gov is not None
        assert gov.source_of_truth == SourceOfTruth.BUSINESS_API
        assert gov.ingestion_mode == IngestionMode.DENY
        assert gov.extension_mode == ExtensionMode.LOCKED

    # Relationship-level: Layer 1 relationships are DENY for ingestion
    rel_gov = registry.resolve_relationship("course", "has_course_schedule", "course_schedule")
    assert rel_gov is not None
    assert rel_gov.source_of_truth == SourceOfTruth.BUSINESS_API
    assert rel_gov.ingestion_mode == IngestionMode.DENY

    # Newly added Layer 1 entities from Step 0 gap
    l1_names = {e["technical_name"] for e in LAYER_1_ENTITY_TYPES}
    assert {"course_staff_assignment", "class_session"}.issubset(l1_names)
    assert "course_staff_assignment" in LAYER_1_PROPERTIES
    assert "class_session" in LAYER_1_PROPERTIES


def test_layer2_metadata_and_schema_predicate_ownership_boundaries():
    registry = OwnershipRegistry.from_seeds()

    policy_gov = registry.resolve_entity("policy_rule")
    assert policy_gov is not None
    assert policy_gov.source_of_truth == SourceOfTruth.CORE_KNOWLEDGE
    assert policy_gov.ingestion_mode == IngestionMode.ALLOW
    assert policy_gov.extension_mode == ExtensionMode.EXTENSIBLE_WITH_REVIEW
    assert policy_gov.review_required is True

    discount_gov = registry.resolve_entity("discount_rule")
    assert discount_gov is not None
    assert discount_gov.ingestion_mode == IngestionMode.STRUCTURED_ONLY

    # Layer 1 class_schedule.start_time is denied by schema & predicate
    denied_schedule = registry.check_fact_ingestion(
        "class_schedule",
        "start_time",
        statement="Lớp thiếu nhi học thứ 2 lúc 18:00 tại cơ sở 1.",
    )
    assert denied_schedule.allowed is False
    assert denied_schedule.verdict == OwnershipVerdict.DENY
    assert denied_schedule.code == "OWNERSHIP_BUSINESS_API_DENY"

    # Valid admission policy & training philosophy are allowed by schema & predicate
    allowed_policy = registry.check_fact_ingestion(
        "policy_rule",
        "summary",
        source_type="FORM",
        form_type="policy-rule",
        scope_key="admission_onboarding",
        statement="Học viên mới được đăng ký học thử miễn phí 1 buổi trước khi nhập học.",
    )
    assert allowed_policy.allowed is True
    assert allowed_policy.verdict == OwnershipVerdict.ALLOW

    # Schema distinction: student BeltExam result (belt_exam.exam_result) vs belt_rank.exam_requirements
    denied_student_exam = registry.check_fact_ingestion(
        "belt_exam",
        "exam_result",
        statement="Học viên Nguyễn Văn A đã đạt kết quả thi đai quý 2.",
    )
    assert denied_student_exam.allowed is False
    assert denied_student_exam.verdict == OwnershipVerdict.DENY

    allowed_exam_procedure = registry.check_fact_ingestion(
        "belt_rank",
        "exam_requirements",
        statement="Quy trình thi lên đai vàng gồm bài quyền số 1 và thể lực cơ bản.",
    )
    assert allowed_exam_procedure.allowed is True
    assert allowed_exam_procedure.verdict == OwnershipVerdict.ALLOW


def test_regression_under_6_assessment_policy_with_sat_hach_is_accepted():
    """1. Chính sách sát hạch học viên dưới 6 tuổi phải được chấp nhận (không bị chặn bởi từ 'sát hạch')."""
    mapper = Layer2FactMapper()
    form = PolicyRuleFormRequest(
        tenant_id="00000000-0000-4000-8000-000000000001",
        policy_code="policy-under-6",
        category=PolicyCategory.TRIAL,
        title="Điều kiện tiếp nhận học viên dưới 6 tuổi",
        summary=(
            "Đối với học viên dưới 6 tuổi, học viên cần đạt yêu cầu sát hạch của câu lạc bộ "
            "trước khi được tiếp nhận tập luyện. Phụ huynh cần liên hệ trung tâm để được hướng dẫn "
            "về việc đánh giá khả năng đáp ứng yêu cầu tập luyện của trẻ."
        ),
        conditions=["Học viên dưới 6 tuổi.", "Đạt yêu cầu sát hạch của câu lạc bộ."],
    )
    fact_record = mapper.map_policy_rule(form)
    candidates = mapper.to_candidate_facts(fact_record, form_type="policy-rule")

    pipeline = KnowledgeGovernancePipeline()
    eval_res = pipeline.evaluate(
        tenant_id=form.tenant_id,
        scope_key="admission_onboarding",
        raw_text=form.summary,
        candidate_facts=candidates,
        skip_scope_keyword_check=True,
    )
    assert eval_res.overall_severity == GateSeverity.PASS
    assert len(eval_res.accepted_facts) == 1
    assert len(eval_res.rejected_facts) == 0


def test_regression_belt_exam_policy_and_tuition_policy_accepted():
    """2 & 3. Chính sách thi lên đai và chính sách học phí phải được chấp nhận."""
    registry = OwnershipRegistry.from_seeds()

    belt_exam_policy = registry.check_fact_ingestion(
        "policy_rule",
        predicate="summary",
        source_type="FORM",
        form_type="policy-rule",
        scope_key="belt_and_exam",
        statement="Học viên thi lên đai cần tham dự kỳ sát hạch định kỳ hàng quý và đạt điểm kỹ thuật.",
    )
    assert belt_exam_policy.verdict == OwnershipVerdict.ALLOW
    assert belt_exam_policy.allowed is True

    tuition_policy = registry.check_fact_ingestion(
        "policy_rule",
        predicate="summary",
        source_type="FORM",
        form_type="policy-rule",
        scope_key="tuition_and_discount",
        statement="Chính sách hoàn học phí và bảo lưu gói tập khi học viên nghỉ ốm dài ngày.",
    )
    assert tuition_policy.verdict == OwnershipVerdict.ALLOW
    assert tuition_policy.allowed is True


@pytest.mark.parametrize(
    ("entity_type", "predicate"),
    [
        ("belt_exam", "exam_result"),
        ("training_plan", "tuition_debt"),
        ("training_plan", "debt_amount"),
        ("class_session", "attendance_status"),
        ("class_schedule", "start_time"),
        ("class_schedule", "end_time"),
        ("training_plan", "base_price"),
        ("training_plan", "final_price"),
        ("course", "capacity"),
        ("branch", "hotline"),
        ("branch", "address"),
    ],
)
def test_regression_business_api_operational_facts_denied(entity_type: str, predicate: str):
    """4 & 5. Kết quả thi đai cá nhân, công nợ, điểm danh, lịch tập và giá gói đào tạo thuộc Business API phải bị từ chối."""
    registry = OwnershipRegistry.from_seeds()
    decision = registry.check_fact_ingestion(
        entity_type,
        predicate=predicate,
        statement="Nội dung văn bản bất kỳ.",
    )
    assert decision.verdict == OwnershipVerdict.DENY
    assert decision.allowed is False
    assert decision.source_of_truth == SourceOfTruth.BUSINESS_API


def test_regression_statement_wording_invariance():
    """6. Thay đổi từ ngữ trong statement không được làm thay đổi quyết định ownership nếu cấu trúc fact hợp lệ không thay đổi."""
    registry = OwnershipRegistry.from_seeds()
    statements = [
        "Nội quy thông thường tại câu lạc bộ.",
        "Học viên Nguyễn Văn A đã đạt kết quả thi đai quý 2 với mức học phí 1.500.000đ lúc 18:00.",
        "Sát hạch đầu vào, điểm danh buổi học, công nợ học phí, sĩ số tối đa 30 học viên, hotline 0987654321.",
    ]
    allow_decisions = [
        registry.check_fact_ingestion(
            "policy_rule",
            predicate="summary",
            source_type="FORM",
            form_type="policy-rule",
            scope_key="admission_onboarding",
            statement=s,
        )
        for s in statements
    ]
    assert all(d.verdict == OwnershipVerdict.ALLOW and d.allowed for d in allow_decisions)

    deny_decisions = [
        registry.check_fact_ingestion(
            "class_schedule",
            predicate="start_time",
            statement=s,
        )
        for s in statements
    ]
    assert all(d.verdict == OwnershipVerdict.DENY and not d.allowed for d in deny_decisions)


@pytest.mark.parametrize(
    "disguised_predicate",
    [
        "start_time",
        "end_time",
        "final_price",
        "base_price",
        "tuition_fee",
        "exam_result",
        "attendance_status",
        "debt_amount",
        "tuition_debt",
        "capacity",
        "hotline",
    ],
)
def test_regression_cannot_bypass_validator_by_mislabeling_as_policy_rule(disguised_predicate: str):
    """7. Không cho phép dữ liệu nghiệp vụ vượt qua validator bằng cách gán nhãn policy_rule sai."""
    registry = OwnershipRegistry.from_seeds()
    decision = registry.check_fact_ingestion(
        "policy_rule",
        predicate=disguised_predicate,
        source_type="FORM",
        form_type="policy-rule",
        scope_key="admission_onboarding",
        statement="Dữ liệu nghiệp vụ giả danh policy_rule.",
    )
    assert decision.verdict == OwnershipVerdict.DENY
    assert decision.allowed is False
    assert decision.code == "OWNERSHIP_SEMANTIC_ALIAS_DENY"

    # Also test form_type / entity_type mismatch anti-bypass
    mismatch_decision = registry.check_fact_ingestion(
        "class_schedule",
        predicate="start_time",
        source_type="FORM",
        form_type="policy-rule",
        scope_key="admission_onboarding",
    )
    assert mismatch_decision.verdict == OwnershipVerdict.DENY
    assert mismatch_decision.code == "OWNERSHIP_FORM_ENTITY_MISMATCH"


def test_regression_missing_or_unverified_predicate_requires_review_and_blocks_auto_publish():
    """8. Fact chưa xác nhận được predicate hợp lệ trong schema trả về REVIEW_REQUIRED và không tự động ghi vào Neo4j."""
    registry = OwnershipRegistry.from_seeds()

    missing_pred = registry.check_fact_ingestion("policy_rule", predicate=None)
    assert missing_pred.verdict == OwnershipVerdict.REVIEW_REQUIRED
    assert missing_pred.allowed is False
    assert missing_pred.code == "OWNERSHIP_MISSING_PREDICATE"

    unknown_pred = registry.check_fact_ingestion("policy_rule", predicate="custom_unregistered_field")
    assert unknown_pred.verdict == OwnershipVerdict.REVIEW_REQUIRED
    assert unknown_pred.allowed is False
    assert unknown_pred.code == "OWNERSHIP_UNVERIFIED_PREDICATE"

    # Pipeline isolates REVIEW_REQUIRED facts from accepted_facts and flags needs_entity_review
    pipeline = KnowledgeGovernancePipeline(ownership_registry=registry)
    eval_res = pipeline.evaluate(
        tenant_id="tenant-a",
        scope_key="admission_onboarding",
        raw_text="Quy định học thử Taekwondo cho học viên mới.",
        candidate_facts=[
            CandidateFact(
                fact_id="f-unverified",
                entity_type="policy_rule",
                predicate="custom_unregistered_field",
                statement="Quy định học thử Taekwondo cho học viên mới.",
                scope_key="admission_onboarding",
            )
        ],
    )
    assert eval_res.overall_severity == GateSeverity.WARN
    assert eval_res.needs_entity_review is True
    assert eval_res.accepted_facts == []
    assert len(eval_res.rejected_facts) == 1
    assert eval_res.rejected_facts[0].code == "OWNERSHIP_UNVERIFIED_PREDICATE"


def test_layer3_proposal_guards_and_compiled_snapshot_roundtrip():
    registry = OwnershipRegistry.from_seeds()

    # Missing tenant -> denied
    no_tenant = registry.check_layer3_proposal(
        "NEW_ENTITY_TYPE", {"technicalName": "summer_camp"}, tenant_id=None, approved=True
    )
    assert no_tenant.allowed is False
    assert no_tenant.code == "TENANT_REQUIRED"

    # Unapproved proposal -> denied
    unapproved = registry.check_layer3_proposal(
        "NEW_ENTITY_TYPE", {"technicalName": "summer_camp"}, tenant_id="t1", approved=False
    )
    assert unapproved.allowed is False
    assert unapproved.code == "PROPOSAL_NOT_APPROVED"

    # Bypass attempt: property equivalent to ClassSchedule.startTime -> denied (ING013)
    bypass = registry.check_layer3_proposal(
        "NEW_PROPERTY",
        {"entityType": "summer_camp", "technicalName": "startTime"},
        tenant_id="t1",
        approved=True,
    )
    assert bypass.allowed is False
    assert bypass.code == "OWNERSHIP_BYPASS_DENIED"

    # Valid approved tenant extension -> allowed
    valid_l3 = registry.check_layer3_proposal(
        "NEW_ENTITY_TYPE",
        {"technicalName": "summer_camp_program"},
        tenant_id="t1",
        approved=True,
    )
    assert valid_l3.allowed is True
    assert valid_l3.source_of_truth == SourceOfTruth.TENANT_EXTENSION

    # Compiled snapshot roundtrip
    compiled_snapshot = {
        "entityTypes": [
            {
                "technicalName": "class_schedule",
                "governance": registry.resolve_entity("class_schedule").to_compiled_dict(),
            }
        ],
        "properties": [
            {
                "entityType": "class_schedule",
                "technicalName": "start_time",
                "governance": registry.resolve_property("class_schedule", "start_time").to_compiled_dict(),
            }
        ],
        "relationships": [],
    }
    roundtrip = OwnershipRegistry.from_compiled_snapshots([compiled_snapshot])
    assert roundtrip.resolve_property("class_schedule", "start_time").ingestion_mode == IngestionMode.DENY
