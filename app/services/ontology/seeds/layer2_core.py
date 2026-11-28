"""Layer 2 — Core Knowledge Ontology serving the 17 GraphRAG & Hybrid FunctionTools.

Ownership: CORE_KNOWLEDGE
Managed via developer seed + Admin deterministic forms (Pydantic schemas).
Never duplicates Layer 1 operational data; links via BusinessRef (`business_id`).
"""

from typing import Any

from app.models import OntologyPropertyDataType, RelationshipCardinality
from app.services.ontology.ownership_registry import (
    ExtensionMode,
    GovernanceMetadata,
    IngestionMode,
    SourceOfTruth,
    VisibilityLevel,
)
from app.services.ontology.seeds.layer1_business import PropertySpec

LAYER_2_LAYER = "LAYER_2_CORE"
LAYER_2_OWNERSHIP = "CORE_KNOWLEDGE"

_LAYER_2_OWNER_AGENTS: dict[str, tuple[str, ...]] = {
    "policy_rule": ("center_info", "admission", "training", "tuition"),
    "center_profile": ("center_info",),
    "facility_item": ("center_info",),
    "class_suitability_profile": ("admission", "training"),
    "enrollment_step": ("admission",),
    "taekwondo_concept": ("training",),
    "poomsae": ("training",),
    "belt_rank": ("training",),
    "discount_rule": ("tuition",),
}


def get_layer2_entity_governance(spec: dict[str, Any]) -> GovernanceMetadata:
    name = str(spec["technical_name"]).casefold()
    ingestion_mode = (
        IngestionMode.STRUCTURED_ONLY
        if name == "discount_rule"
        else IngestionMode.ALLOW
    )
    return GovernanceMetadata(
        layer=LAYER_2_LAYER,
        sourceOfTruth=SourceOfTruth.CORE_KNOWLEDGE,
        ingestionMode=ingestion_mode,
        extensionMode=ExtensionMode.EXTENSIBLE_WITH_REVIEW,
        visibility=VisibilityLevel.PUBLIC,
        ownerAgentKeys=_LAYER_2_OWNER_AGENTS.get(name, ()),
        reviewRequired=True,
        sourceBinding={"service": "be-py", "layer": LAYER_2_LAYER, "entity": name},
    )


def get_layer2_property_governance(
    entity_name: str, property_name: str
) -> GovernanceMetadata:
    ename = entity_name.casefold()
    pname = property_name.casefold()
    ingestion_mode = (
        IngestionMode.STRUCTURED_ONLY
        if ename == "discount_rule"
        else IngestionMode.ALLOW
    )
    return GovernanceMetadata(
        layer=LAYER_2_LAYER,
        sourceOfTruth=SourceOfTruth.CORE_KNOWLEDGE,
        ingestionMode=ingestion_mode,
        extensionMode=ExtensionMode.EXTENSIBLE_WITH_REVIEW,
        visibility=VisibilityLevel.PUBLIC,
        ownerAgentKeys=_LAYER_2_OWNER_AGENTS.get(ename, ()),
        reviewRequired=True,
        sourceBinding={"service": "be-py", "entity": ename, "field": pname},
    )


def get_layer2_relationship_governance(
    source_name: str, rel_name: str, target_name: str
) -> GovernanceMetadata:
    sname = source_name.casefold()
    return GovernanceMetadata(
        layer=LAYER_2_LAYER,
        sourceOfTruth=SourceOfTruth.CORE_KNOWLEDGE,
        ingestionMode=IngestionMode.ALLOW,
        extensionMode=ExtensionMode.EXTENSIBLE_WITH_REVIEW,
        visibility=VisibilityLevel.PUBLIC,
        ownerAgentKeys=_LAYER_2_OWNER_AGENTS.get(sname, ()),
        reviewRequired=True,
        sourceBinding={
            "service": "be-py",
            "relationship": f"{sname}.{rel_name}->{target_name.casefold()}",
        },
    )

LAYER_2_ENTITY_TYPES: tuple[dict[str, Any], ...] = (
    {
        "technical_name": "policy_rule",
        "display_name": "Policy Rule",
        "description": "Quy định/chính sách chuẩn hóa (học thử, giờ mở cửa, nội quy, nghỉ/học bù, thi đai, học phí, thanh toán).",
        "identity_strategy": {"required": ["policy_code"]},
        "metadata": {
            "layer": LAYER_2_LAYER,
            "ownership": LAYER_2_OWNERSHIP,
            "domain": "CoreKnowledge",
        },
    },
    {
        "technical_name": "center_profile",
        "display_name": "Center Profile",
        "description": "Thông tin giới thiệu trung tâm, lịch sử, tầm nhìn, sứ mệnh và các kênh liên hệ ngoài API Java.",
        # There is one Center Profile per tenant. Tenant identity comes from
        # the trusted request context rather than an editable form field.
        "identity_strategy": {"required": []},
        "metadata": {
            "layer": LAYER_2_LAYER,
            "ownership": LAYER_2_OWNERSHIP,
            "domain": "CoreKnowledge",
        },
    },
    {
        "technical_name": "facility_item",
        "display_name": "Facility Item",
        "description": "Cơ sở vật chất, thảm tập, trang thiết bị và tiện ích phòng tập.",
        "identity_strategy": {"required": ["name", "category"]},
        "metadata": {
            "layer": LAYER_2_LAYER,
            "ownership": LAYER_2_OWNERSHIP,
            "domain": "CoreKnowledge",
        },
    },
    {
        "technical_name": "class_suitability_profile",
        "display_name": "Class Suitability Profile",
        "description": "Tiêu chí độ tuổi, mục tiêu tập luyện và nội dung giáo trình chi tiết gắn với Course/TrainingPlan.",
        "identity_strategy": {"required": ["profile_code"]},
        "metadata": {
            "layer": LAYER_2_LAYER,
            "ownership": LAYER_2_OWNERSHIP,
            "domain": "CoreKnowledge",
        },
    },
    {
        "technical_name": "enrollment_step",
        "display_name": "Enrollment Step",
        "description": "Các bước quy trình đăng ký nhập học và hồ sơ cần chuẩn bị.",
        "identity_strategy": {"required": ["step_code"]},
        "metadata": {
            "layer": LAYER_2_LAYER,
            "ownership": LAYER_2_OWNERSHIP,
            "domain": "CoreKnowledge",
        },
    },
    {
        "technical_name": "taekwondo_concept",
        "display_name": "Taekwondo Concept",
        "description": "Kiến thức chuyên môn Taekwondo: đòn thế, luật thi đấu, thuật ngữ và lợi ích tập võ.",
        "identity_strategy": {"required": ["name", "category"]},
        "metadata": {
            "layer": LAYER_2_LAYER,
            "ownership": LAYER_2_OWNERSHIP,
            "domain": "CoreKnowledge",
        },
    },
    {
        "technical_name": "poomsae",
        "display_name": "Poomsae",
        "description": "Bài quyền Taekwondo (số động tác, cấp độ, mô tả).",
        "identity_strategy": {"required": ["name"]},
        "metadata": {
            "layer": LAYER_2_LAYER,
            "ownership": LAYER_2_OWNERSHIP,
            "domain": "CoreKnowledge",
        },
    },
    {
        "technical_name": "belt_rank",
        "display_name": "Belt Rank",
        "description": "Hệ thống cấp đai, thứ tự thăng cấp, ý nghĩa màu đai và yêu cầu thi lên đai.",
        "identity_strategy": {"required": ["name"]},
        "metadata": {
            "layer": LAYER_2_LAYER,
            "ownership": LAYER_2_OWNERSHIP,
            "domain": "CoreKnowledge",
        },
    },
    {
        "technical_name": "discount_rule",
        "display_name": "Discount Rule",
        "description": "Quy tắc ưu đãi, giảm giá theo thời hạn, số học viên hoặc mã khuyến mãi phục vụ tính học phí deterministic.",
        "identity_strategy": {"required": ["rule_code"]},
        "metadata": {
            "layer": LAYER_2_LAYER,
            "ownership": LAYER_2_OWNERSHIP,
            "domain": "CoreKnowledge",
        },
    },
)

LAYER_2_PROPERTIES: dict[str, tuple[PropertySpec, ...]] = {
    "policy_rule": (
        ("policy_code", "Policy Code", OntologyPropertyDataType.STRING, True, False, {}, "Mã quy định duy nhất"),
        (
            "category",
            "Category",
            OntologyPropertyDataType.STRING,
            True,
            False,
            {
                "enum": [
                    "TRIAL",
                    "OPENING_HOURS",
                    "TRAINING_CONDUCT",
                    "LEAVE_MAKEUP",
                    "BELT_EXAM",
                    "TUITION",
                    "PAYMENT",
                ]
            },
            "Phân loại chính sách",
        ),
        ("title", "Title", OntologyPropertyDataType.STRING, True, False, {}, "Tiêu đề chính sách"),
        ("summary", "Summary", OntologyPropertyDataType.STRING, True, False, {}, "Tóm tắt nội dung"),
        ("conditions", "Conditions", OntologyPropertyDataType.JSON, False, False, {}, "Danh sách điều kiện áp dụng"),
        ("operating_hours", "Operating Hours", OntologyPropertyDataType.JSON, False, False, {}, "Giờ mở cửa theo thứ (dùng cho OPENING_HOURS)"),
        ("payment_methods", "Payment Methods", OntologyPropertyDataType.JSON, False, False, {}, "Thông tin chuyển khoản/tiền mặt (dùng cho PAYMENT)"),
        ("effective_from", "Effective From", OntologyPropertyDataType.DATE, False, False, {}, "Ngày bắt đầu hiệu lực"),
        ("effective_to", "Effective To", OntologyPropertyDataType.DATE, False, False, {}, "Ngày hết hiệu lực"),
    ),
    "center_profile": (
        ("title", "Title", OntologyPropertyDataType.STRING, True, False, {}, "Tên hiển thị trung tâm"),
        ("history", "History", OntologyPropertyDataType.STRING, False, False, {}, "Lịch sử hình thành"),
        ("vision", "Vision", OntologyPropertyDataType.STRING, False, False, {}, "Tầm nhìn"),
        ("mission", "Mission", OntologyPropertyDataType.STRING, False, False, {}, "Sứ mệnh"),
        ("email", "Email", OntologyPropertyDataType.STRING, False, False, {}, "Email liên hệ"),
        ("zalo_url", "Zalo URL", OntologyPropertyDataType.STRING, False, False, {}, "Kênh Zalo chính thức"),
        ("facebook_url", "Facebook URL", OntologyPropertyDataType.STRING, False, False, {}, "Fanpage Facebook"),
        ("website_url", "Website URL", OntologyPropertyDataType.STRING, False, False, {}, "Website chính thức"),
        ("support_hours", "Support Hours", OntologyPropertyDataType.STRING, False, False, {}, "Khung giờ trực tư vấn"),
    ),
    "facility_item": (
        ("name", "Facility Name", OntologyPropertyDataType.STRING, True, False, {}, "Tên trang thiết bị / tiện ích"),
        (
            "category",
            "Category",
            OntologyPropertyDataType.STRING,
            True,
            False,
            {"enum": ["MAT", "ARMOR", "ROOM_AMENITY", "SAFETY", "EQUIPMENT"]},
            "Nhóm cơ sở vật chất",
        ),
        ("quantity_note", "Quantity Note", OntologyPropertyDataType.STRING, False, False, {}, "Ghi chú số lượng / quy mô"),
        ("description", "Description", OntologyPropertyDataType.STRING, True, False, {}, "Mô tả chi tiết"),
    ),
    "class_suitability_profile": (
        ("profile_code", "Profile Code", OntologyPropertyDataType.STRING, True, False, {}, "Mã hồ sơ lớp học"),
        ("min_age", "Min Age", OntologyPropertyDataType.INTEGER, True, False, {"min": 0, "max": 100}, "Độ tuổi tối thiểu"),
        ("max_age", "Max Age", OntologyPropertyDataType.INTEGER, True, False, {"min": 0, "max": 100}, "Độ tuổi tối đa"),
        (
            "target_goals",
            "Target Goals",
            OntologyPropertyDataType.STRING,
            True,
            True,
            {"enum": ["FITNESS", "SELF_DEFENSE", "COMPETITION", "RECREATIONAL", "DISCIPLINE"]},
            "Mục tiêu tập luyện phù hợp",
        ),
        ("curriculum_summary", "Curriculum Summary", OntologyPropertyDataType.STRING, False, False, {}, "Tóm tắt giáo trình"),
        ("curriculum_modules", "Curriculum Modules", OntologyPropertyDataType.JSON, False, False, {}, "Các học phần/chủ đề chi tiết"),
        ("prerequisites", "Prerequisites", OntologyPropertyDataType.STRING, False, False, {}, "Yêu cầu đầu vào bổ sung"),
    ),
    "enrollment_step": (
        ("step_code", "Step Code", OntologyPropertyDataType.STRING, True, False, {}, "Mã bước đăng ký"),
        ("step_order", "Step Order", OntologyPropertyDataType.INTEGER, True, False, {"min": 1}, "Thứ tự bước"),
        ("title", "Title", OntologyPropertyDataType.STRING, True, False, {}, "Tên bước"),
        ("description", "Description", OntologyPropertyDataType.STRING, True, False, {}, "Hướng dẫn chi tiết"),
        ("required_documents", "Required Documents", OntologyPropertyDataType.STRING, False, True, {}, "Hồ sơ cần chuẩn bị"),
        ("channel", "Channel", OntologyPropertyDataType.STRING, False, False, {"enum": ["ONLINE", "OFFLINE", "HYBRID"]}, "Kênh thực hiện"),
    ),
    "taekwondo_concept": (
        ("name", "Concept Name", OntologyPropertyDataType.STRING, True, False, {}, "Tên khái niệm / đòn thế / lợi ích"),
        ("korean_name", "Korean Name", OntologyPropertyDataType.STRING, False, False, {}, "Tên tiếng Hàn"),
        (
            "category",
            "Category",
            OntologyPropertyDataType.STRING,
            True,
            False,
            {"enum": ["TECHNIQUE", "RULE", "TERMINOLOGY", "BENEFIT"]},
            "Phân loại kiến thức",
        ),
        (
            "target_audience",
            "Target Audience",
            OntologyPropertyDataType.STRING,
            False,
            False,
            {"enum": ["KIDS", "TEENS", "ADULTS", "ALL"]},
            "Đối tượng áp dụng",
        ),
        ("description", "Description", OntologyPropertyDataType.STRING, True, False, {}, "Nội dung giải thích"),
    ),
    "poomsae": (
        ("name", "Poomsae Name", OntologyPropertyDataType.STRING, True, False, {}, "Tên bài quyền"),
        ("korean_name", "Korean Name", OntologyPropertyDataType.STRING, False, False, {}, "Tên tiếng Hàn"),
        ("movement_count", "Movement Count", OntologyPropertyDataType.INTEGER, False, False, {"min": 1}, "Số động tác"),
        ("level", "Level", OntologyPropertyDataType.STRING, False, False, {}, "Cấp độ bài quyền"),
        ("description", "Description", OntologyPropertyDataType.STRING, True, False, {}, "Mô tả bài quyền"),
    ),
    "belt_rank": (
        ("name", "Belt Rank Name", OntologyPropertyDataType.STRING, True, False, {}, "Tên cấp đai"),
        ("rank_order", "Rank Order", OntologyPropertyDataType.INTEGER, True, False, {"min": 1}, "Thứ tự cấp đai"),
        ("color", "Color", OntologyPropertyDataType.STRING, True, False, {}, "Màu đai"),
        ("meaning", "Meaning", OntologyPropertyDataType.STRING, False, False, {}, "Ý nghĩa màu đai"),
        ("min_training_months", "Min Training Months", OntologyPropertyDataType.INTEGER, False, False, {"min": 0}, "Thời gian tập tối thiểu (tháng)"),
        ("exam_requirements", "Exam Requirements", OntologyPropertyDataType.STRING, False, True, {}, "Nội dung kiểm tra lên đai"),
    ),
    "discount_rule": (
        ("rule_code", "Rule Code", OntologyPropertyDataType.STRING, True, False, {}, "Mã quy tắc ưu đãi"),
        ("name", "Rule Name", OntologyPropertyDataType.STRING, True, False, {}, "Tên chương trình ưu đãi"),
        (
            "discount_type",
            "Discount Type",
            OntologyPropertyDataType.STRING,
            True,
            False,
            {"enum": ["PERCENTAGE", "FIXED_AMOUNT"]},
            "Loại giảm giá",
        ),
        ("discount_value", "Discount Value", OntologyPropertyDataType.FLOAT, True, False, {"min": 0}, "Giá trị giảm (% hoặc VNĐ)"),
        ("min_months", "Min Months", OntologyPropertyDataType.INTEGER, True, False, {"min": 1}, "Số tháng tối thiểu"),
        ("min_students", "Min Students", OntologyPropertyDataType.INTEGER, True, False, {"min": 1}, "Số học viên tối thiểu"),
        ("promo_code", "Promo Code", OntologyPropertyDataType.STRING, False, False, {}, "Mã khuyến mãi (nếu có)"),
        ("stackable", "Stackable", OntologyPropertyDataType.BOOLEAN, True, False, {}, "Cho phép cộng dồn hay không"),
        ("priority", "Priority", OntologyPropertyDataType.INTEGER, True, False, {"min": 0}, "Độ ưu tiên áp dụng"),
        ("applicable_plan_ids", "Applicable Plan IDs", OntologyPropertyDataType.UUID, False, True, {}, "Danh sách training_plan_id áp dụng"),
        ("valid_from", "Valid From", OntologyPropertyDataType.DATE, False, False, {}, "Ngày bắt đầu"),
        ("valid_to", "Valid To", OntologyPropertyDataType.DATE, False, False, {}, "Ngày kết thúc"),
        ("description", "Description", OntologyPropertyDataType.STRING, False, False, {}, "Mô tả điều kiện ưu đãi"),
    ),
}

LAYER_2_RELATIONSHIPS: tuple[tuple[str, str, str, RelationshipCardinality, str], ...] = (
    ("policy_rule", "applies_to_branch", "branch", RelationshipCardinality.MANY_TO_MANY, "Chính sách áp dụng riêng cho cơ sở"),
    ("center_profile", "describes_branch", "branch", RelationshipCardinality.MANY_TO_MANY, "Hồ sơ bổ sung thông tin cho cơ sở"),
    ("facility_item", "located_at_branch", "branch", RelationshipCardinality.MANY_TO_ONE, "Cơ sở vật chất thuộc cơ sở"),
    ("class_suitability_profile", "profiles_course", "course", RelationshipCardinality.MANY_TO_ONE, "Hồ sơ tư vấn gắn với Course Layer 1"),
    ("class_suitability_profile", "profiles_plan", "training_plan", RelationshipCardinality.MANY_TO_MANY, "Hồ sơ giáo trình/tư vấn gắn với TrainingPlan Layer 1"),
    ("enrollment_step", "applies_to_plan", "training_plan", RelationshipCardinality.MANY_TO_MANY, "Bước đăng ký áp dụng cho gói đào tạo"),
    ("belt_rank", "requires_concept", "taekwondo_concept", RelationshipCardinality.MANY_TO_MANY, "Cấp đai yêu cầu kỹ thuật/kiến thức"),
    ("belt_rank", "requires_poomsae", "poomsae", RelationshipCardinality.MANY_TO_MANY, "Cấp đai yêu cầu bài quyền"),
    ("belt_rank", "next_rank", "belt_rank", RelationshipCardinality.ONE_TO_ONE, "Lộ trình thăng cấp đai kế tiếp"),
    ("discount_rule", "applies_to_plan", "training_plan", RelationshipCardinality.MANY_TO_MANY, "Quy tắc ưu đãi áp dụng cho gói học phí"),
    ("discount_rule", "applies_to_branch", "branch", RelationshipCardinality.MANY_TO_MANY, "Quy tắc ưu đãi áp dụng tại cơ sở"),
)

LAYER_2_ALIASES: dict[str, tuple[str, ...]] = {
    "policy_rule": ("noi quy", "chinh sach", "hoc thu", "gio mo cua", "bao luu", "hoan phi", "thanh toan"),
    "center_profile": ("gioi thieu", "lich su", "tam nhin", "su menh", "lien he", "facebook", "zalo"),
    "facility_item": ("co so vat chat", "tham tap", "phong tap", "trang thiet bi"),
    "class_suitability_profile": ("giao trinh", "do tuoi", "goi y lop", "muc tieu tap"),
    "enrollment_step": ("quy trinh dang ky", "ho so nhap hoc"),
    "taekwondo_concept": ("don the", "ky thuat", "luat thi dau", "loi ich"),
    "poomsae": ("bai quyen", "quyen"),
    "belt_rank": ("cap dai", "mau dai", "dai trang", "dai den", "thang cap"),
    "discount_rule": ("uu dai", "khuyen mai", "giam gia", "hoc bong"),
}

# 5 shared Knowledge Scopes (never 1:1 per FunctionTool)
LAYER_2_SCOPE_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "center_overview": (
        "Giới thiệu trung tâm, giờ mở cửa, cơ sở vật chất và kênh liên hệ mở rộng.",
        ("center_profile", "facility_item", "policy_rule", "branch"),
    ),
    "admission_onboarding": (
        "Chính sách học thử, tư vấn chọn lớp theo độ tuổi/mục tiêu và quy trình đăng ký.",
        ("policy_rule", "class_suitability_profile", "enrollment_step", "course", "training_plan", "branch"),
    ),
    "training_knowledge": (
        "Kiến thức chuyên môn Taekwondo, lợi ích tập võ, giáo trình chi tiết và nội quy lớp học.",
        ("taekwondo_concept", "poomsae", "class_suitability_profile", "policy_rule", "course", "training_plan", "branch"),
    ),
    "belt_and_exam": (
        "Hệ thống cấp đai, lộ trình thăng cấp và quy định thi lên đai.",
        ("belt_rank", "taekwondo_concept", "poomsae", "policy_rule", "belt_exam"),
    ),
    "tuition_and_discount": (
        "Chính sách học phí, quy tắc ưu đãi deterministic và phương thức thanh toán.",
        ("policy_rule", "discount_rule", "training_plan", "training_plan_addon", "branch"),
    ),
}

# Canonical mapping from the 17 FunctionTools to the 5 shared Knowledge Scopes
TOOL_SCOPE_MAP: dict[str, str] = {
    # 12 Pure GraphRAG tools
    "search_trial_policy": "admission_onboarding",
    "search_center_knowledge": "center_overview",
    "search_opening_hours": "center_overview",
    "search_center_facilities": "center_overview",
    "search_training_knowledge": "training_knowledge",
    "search_training_benefits": "training_knowledge",
    "search_belt_system": "belt_and_exam",
    "search_exam_policy": "belt_and_exam",
    "search_training_policy": "training_knowledge",
    "search_tuition_policy": "tuition_and_discount",
    "search_discount_policy": "tuition_and_discount",
    "search_payment_policy": "tuition_and_discount",
    # 5 Hybrid (API + GraphRAG) tools
    "recommend_class": "admission_onboarding",
    "search_registration_process": "admission_onboarding",
    "search_contact_information": "center_overview",
    "search_training_curriculum": "training_knowledge",
    "calculate_tuition": "tuition_and_discount",
}

__all__ = [
    "LAYER_2_ALIASES",
    "LAYER_2_ENTITY_TYPES",
    "LAYER_2_LAYER",
    "LAYER_2_OWNERSHIP",
    "LAYER_2_PROPERTIES",
    "LAYER_2_RELATIONSHIPS",
    "LAYER_2_SCOPE_SPECS",
    "TOOL_SCOPE_MAP",
    "get_layer2_entity_governance",
    "get_layer2_property_governance",
    "get_layer2_relationship_governance",
]
