"""Layer 1 — Business Ontology mapped directly from `ai-receptionist-web-be-java`.

Ownership: BUSINESS_API
Sync mode: REFERENCE_ONLY (do not bulk-sync operational records into Neo4j;
only create lightweight reference nodes keyed by `business_id` when Layer 2/3
knowledge needs to link to a Java entity).
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

LAYER_1_LAYER = "LAYER_1_BUSINESS"
LAYER_1_OWNERSHIP = "BUSINESS_API"
LAYER_1_SYNC_MODE = "REFERENCE_ONLY"

_LAYER_1_OWNER_AGENTS: dict[str, tuple[str, ...]] = {
    "branch": ("center_info", "admission", "tuition"),
    "person": ("center_info", "training"),
    "position": ("center_info",),
    "course": ("admission", "training"),
    "class_schedule": ("training", "admission"),
    "course_schedule": ("training", "admission"),
    "course_staff_assignment": ("center_info", "training"),
    "class_session": ("training",),
    "training_plan": ("admission", "tuition", "training"),
    "training_plan_addon": ("tuition",),
    "training_plan_requirement": ("admission", "training"),
    "training_plan_access_rule": ("admission", "training"),
    "belt_exam": ("training",),
}

_LAYER_1_REFERENCE_ID_FIELDS: dict[str, str] = {
    "branch": "branch_id",
    "person": "person_id",
    "position": "position_id",
    "course": "course_id",
    "class_schedule": "schedule_id",
    "course_schedule": "course_schedule_id",
    "course_staff_assignment": "assignment_id",
    "class_session": "session_id",
    "training_plan": "training_plan_id",
    "training_plan_addon": "training_plan_addon_id",
    "training_plan_requirement": "training_plan_requirement_id",
    "training_plan_access_rule": "training_plan_access_rule_id",
    "belt_exam": "belt_exam_id",
}


def _l1_entity_meta(
    technical_name: str,
    domain: str,
    entity_fqn: str,
    endpoints: list[str],
    id_field: str,
) -> dict[str, Any]:
    source_binding = {
        "service": "be-java",
        "entity": entity_fqn,
        "endpoints": endpoints,
        "id_field": id_field,
    }
    return {
        "layer": LAYER_1_LAYER,
        "ownership": LAYER_1_OWNERSHIP,
        "sync_mode": LAYER_1_SYNC_MODE,
        "domain": domain,
        "source_binding": source_binding,
        "sourceOfTruth": SourceOfTruth.BUSINESS_API.value,
        "ingestionMode": IngestionMode.REFERENCE_ONLY.value,
        "extensionMode": ExtensionMode.LOCKED.value,
        "visibility": VisibilityLevel.PUBLIC.value,
        "ownerAgentKeys": list(_LAYER_1_OWNER_AGENTS.get(technical_name, ())),
        "reviewRequired": False,
        "sourceBinding": source_binding,
    }


def get_layer1_entity_governance(spec: dict[str, Any]) -> GovernanceMetadata:
    meta = spec["metadata"]
    name = str(spec["technical_name"]).casefold()
    owner_agents = tuple(meta.get("ownerAgentKeys") or _LAYER_1_OWNER_AGENTS.get(name, ()))
    return GovernanceMetadata(
        layer=LAYER_1_LAYER,
        sourceOfTruth=SourceOfTruth.BUSINESS_API,
        ingestionMode=IngestionMode.REFERENCE_ONLY,
        extensionMode=ExtensionMode.LOCKED,
        visibility=VisibilityLevel.PUBLIC,
        ownerAgentKeys=owner_agents,
        reviewRequired=False,
        sourceBinding=meta.get("sourceBinding") or meta.get("source_binding"),
    )


def get_layer1_property_governance(
    entity_name: str, property_name: str
) -> GovernanceMetadata:
    ename = entity_name.casefold()
    pname = property_name.casefold()
    is_ref_id = _LAYER_1_REFERENCE_ID_FIELDS.get(ename) == pname
    return GovernanceMetadata(
        layer=LAYER_1_LAYER,
        sourceOfTruth=SourceOfTruth.BUSINESS_API,
        ingestionMode=(
            IngestionMode.REFERENCE_ONLY if is_ref_id else IngestionMode.DENY
        ),
        extensionMode=ExtensionMode.LOCKED,
        visibility=VisibilityLevel.PUBLIC,
        ownerAgentKeys=_LAYER_1_OWNER_AGENTS.get(ename, ()),
        reviewRequired=False,
        sourceBinding={"service": "be-java", "entity": ename, "field": pname},
    )


def get_layer1_relationship_governance(
    source_name: str, rel_name: str, target_name: str
) -> GovernanceMetadata:
    sname = source_name.casefold()
    return GovernanceMetadata(
        layer=LAYER_1_LAYER,
        sourceOfTruth=SourceOfTruth.BUSINESS_API,
        ingestionMode=IngestionMode.DENY,
        extensionMode=ExtensionMode.LOCKED,
        visibility=VisibilityLevel.PUBLIC,
        ownerAgentKeys=_LAYER_1_OWNER_AGENTS.get(sname, ()),
        reviewRequired=False,
        sourceBinding={
            "service": "be-java",
            "relationship": f"{sname}.{rel_name}->{target_name.casefold()}",
        },
    )

# Tuple format: (technical_name, display_name, data_type, required, multi_value, constraints, description)
PropertySpec = tuple[
    str, str, OntologyPropertyDataType, bool, bool, dict[str, Any], str
]

LAYER_1_ENTITY_TYPES: tuple[dict[str, Any], ...] = (
    {
        "technical_name": "branch",
        "display_name": "Branch",
        "description": "Cơ sở/chi nhánh hoạt động của trung tâm Taekwondo (be-java Core\\Branch).",
        "identity_strategy": {"required": ["branch_id"], "businessKey": "branch_id"},
        "metadata": {
            "layer": LAYER_1_LAYER,
            "ownership": LAYER_1_OWNERSHIP,
            "sync_mode": LAYER_1_SYNC_MODE,
            "domain": "Core",
            "source_binding": {
                "service": "be-java",
                "entity": "com.dat.ai_receptionist_web.domain.Core.Branch",
                "endpoints": ["GET /api/v1/branches", "GET /api/v1/branches/{id}"],
                "id_field": "branchId",
            },
        },
    },
    {
        "technical_name": "person",
        "display_name": "Person (Public Staff/Coach)",
        "description": "Thông tin công khai của huấn luyện viên/nhân sự (be-java Core\\Person, loại trừ dữ liệu nhạy cảm).",
        "identity_strategy": {"required": ["person_id"], "businessKey": "person_id"},
        "metadata": {
            "layer": LAYER_1_LAYER,
            "ownership": LAYER_1_OWNERSHIP,
            "sync_mode": LAYER_1_SYNC_MODE,
            "domain": "Core",
            "source_binding": {
                "service": "be-java",
                "entity": "com.dat.ai_receptionist_web.domain.Core.Person",
                "endpoints": ["GET /api/v1/persons", "GET /api/v1/persons/{id}"],
                "id_field": "personId",
            },
        },
    },
    {
        "technical_name": "position",
        "display_name": "Position",
        "description": "Chức vụ / vai trò chuyên môn của nhân sự (be-java Core\\Position).",
        "identity_strategy": {"required": ["position_id"], "businessKey": "position_id"},
        "metadata": {
            "layer": LAYER_1_LAYER,
            "ownership": LAYER_1_OWNERSHIP,
            "sync_mode": LAYER_1_SYNC_MODE,
            "domain": "Core",
            "source_binding": {
                "service": "be-java",
                "entity": "com.dat.ai_receptionist_web.domain.Core.Position",
                "endpoints": ["GET /api/v1/positions"],
                "id_field": "positionId",
            },
        },
    },
    {
        "technical_name": "course",
        "display_name": "Course",
        "description": "Lớp học / khóa học chính thức tại trung tâm (be-java Catalog\\Course).",
        "identity_strategy": {"required": ["course_id"], "businessKey": "course_id"},
        "metadata": {
            "layer": LAYER_1_LAYER,
            "ownership": LAYER_1_OWNERSHIP,
            "sync_mode": LAYER_1_SYNC_MODE,
            "domain": "Catalog",
            "source_binding": {
                "service": "be-java",
                "entity": "com.dat.ai_receptionist_web.domain.Catalog.Course",
                "endpoints": ["GET /api/v1/courses", "GET /api/v1/courses/{id}"],
                "id_field": "courseId",
            },
        },
    },
    {
        "technical_name": "class_schedule",
        "display_name": "Class Schedule",
        "description": "Khung giờ học cố định theo thứ và cơ sở (be-java Catalog\\ClassSchedule).",
        "identity_strategy": {"required": ["schedule_id"], "businessKey": "schedule_id"},
        "metadata": {
            "layer": LAYER_1_LAYER,
            "ownership": LAYER_1_OWNERSHIP,
            "sync_mode": LAYER_1_SYNC_MODE,
            "domain": "Catalog",
            "source_binding": {
                "service": "be-java",
                "entity": "com.dat.ai_receptionist_web.domain.Catalog.ClassSchedule",
                "endpoints": ["GET /api/v1/class-schedules"],
                "id_field": "scheduleId",
            },
        },
    },
    {
        "technical_name": "course_schedule",
        "display_name": "Course Schedule",
        "description": "Lịch vận hành thực tế gắn Course với ClassSchedule (be-java Catalog\\CourseSchedule).",
        "identity_strategy": {
            "required": ["course_schedule_id"],
            "businessKey": "course_schedule_id",
        },
        "metadata": {
            "layer": LAYER_1_LAYER,
            "ownership": LAYER_1_OWNERSHIP,
            "sync_mode": LAYER_1_SYNC_MODE,
            "domain": "Catalog",
            "source_binding": {
                "service": "be-java",
                "entity": "com.dat.ai_receptionist_web.domain.Catalog.CourseSchedule",
                "endpoints": ["GET /api/v1/course-schedules"],
                "id_field": "courseScheduleId",
            },
        },
    },
    {
        "technical_name": "training_plan",
        "display_name": "Training Plan",
        "description": "Gói học phí / gói đào tạo chính thức (be-java Catalog\\TrainingPlan).",
        "identity_strategy": {
            "required": ["training_plan_id"],
            "businessKey": "training_plan_id",
        },
        "metadata": {
            "layer": LAYER_1_LAYER,
            "ownership": LAYER_1_OWNERSHIP,
            "sync_mode": LAYER_1_SYNC_MODE,
            "domain": "Catalog",
            "source_binding": {
                "service": "be-java",
                "entity": "com.dat.ai_receptionist_web.domain.Catalog.TrainingPlan",
                "endpoints": [
                    "GET /api/v1/training-plans",
                    "GET /api/v1/training-plans/{id}",
                    "GET /api/v1/training-plans/registration-catalog",
                ],
                "id_field": "trainingPlanId",
            },
        },
    },
    {
        "technical_name": "training_plan_addon",
        "display_name": "Training Plan Addon",
        "description": "Gói bổ sung buổi tập / phụ phí đi kèm TrainingPlan (be-java Catalog\\TrainingPlanAddon).",
        "identity_strategy": {
            "required": ["training_plan_addon_id"],
            "businessKey": "training_plan_addon_id",
        },
        "metadata": {
            "layer": LAYER_1_LAYER,
            "ownership": LAYER_1_OWNERSHIP,
            "sync_mode": LAYER_1_SYNC_MODE,
            "domain": "Catalog",
            "source_binding": {
                "service": "be-java",
                "entity": "com.dat.ai_receptionist_web.domain.Catalog.TrainingPlanAddon",
                "endpoints": [
                    "GET /api/v1/training-plan-addons",
                    "GET /api/v1/training-plan-addons/{id}",
                ],
                "id_field": "trainingPlanAddonId",
            },
        },
    },
    {
        "technical_name": "training_plan_requirement",
        "display_name": "Training Plan Requirement",
        "description": "Yêu cầu số buổi mỗi tuần theo loại buổi của gói đào tạo (be-java Catalog\\TrainingPlanRequirement).",
        "identity_strategy": {
            "required": ["training_plan_requirement_id"],
            "businessKey": "training_plan_requirement_id",
        },
        "metadata": {
            "layer": LAYER_1_LAYER,
            "ownership": LAYER_1_OWNERSHIP,
            "sync_mode": LAYER_1_SYNC_MODE,
            "domain": "Catalog",
            "source_binding": {
                "service": "be-java",
                "entity": "com.dat.ai_receptionist_web.domain.Catalog.TrainingPlanRequirement",
                "endpoints": ["GET /api/v1/training-plan-requirements"],
                "id_field": "trainingPlanRequirementId",
            },
        },
    },
    {
        "technical_name": "training_plan_access_rule",
        "display_name": "Training Plan Access Rule",
        "description": "Quy tắc ánh xạ gói đào tạo tới lớp học được phép tham gia (be-java Catalog\\TrainingPlanAccessRule).",
        "identity_strategy": {
            "required": ["training_plan_access_rule_id"],
            "businessKey": "training_plan_access_rule_id",
        },
        "metadata": {
            "layer": LAYER_1_LAYER,
            "ownership": LAYER_1_OWNERSHIP,
            "sync_mode": LAYER_1_SYNC_MODE,
            "domain": "Catalog",
            "source_binding": {
                "service": "be-java",
                "entity": "com.dat.ai_receptionist_web.domain.Catalog.TrainingPlanAccessRule",
                "endpoints": ["GET /api/v1/training-plan-access-rules"],
                "id_field": "trainingPlanAccessRuleId",
            },
        },
    },
    {
        "technical_name": "belt_exam",
        "display_name": "Belt Exam",
        "description": "Thông tin kỳ thi thăng đai (be-java Training\\BeltExam, chỉ metadata kỳ thi).",
        "identity_strategy": {
            "required": ["belt_exam_id"],
            "businessKey": "belt_exam_id",
        },
        "metadata": _l1_entity_meta(
            "belt_exam",
            "Training",
            "com.dat.ai_receptionist_web.domain.Training.BeltExam",
            ["GET /api/v1/belt-exams"],
            "beltExamId",
        ),
    },
    {
        "technical_name": "course_staff_assignment",
        "display_name": "Course Staff Assignment",
        "description": "Phân công huấn luyện viên phụ trách lịch khóa học (be-java Training\\CourseStaffAssignment).",
        "identity_strategy": {
            "required": ["assignment_id"],
            "businessKey": "assignment_id",
        },
        "metadata": _l1_entity_meta(
            "course_staff_assignment",
            "Training",
            "com.dat.ai_receptionist_web.domain.Training.CourseStaffAssignment",
            ["GET /api/v1/course-staff-assignments"],
            "assignmentId",
        ),
    },
    {
        "technical_name": "class_session",
        "display_name": "Class Session",
        "description": "Buổi học cụ thể theo ngày trên lịch trung tâm (be-java Training\\ClassSession).",
        "identity_strategy": {
            "required": ["session_id"],
            "businessKey": "session_id",
        },
        "metadata": _l1_entity_meta(
            "class_session",
            "Training",
            "com.dat.ai_receptionist_web.domain.Training.ClassSession",
            ["GET /api/v1/class-sessions/calendar"],
            "sessionId",
        ),
    },
)

LAYER_1_PROPERTIES: dict[str, tuple[PropertySpec, ...]] = {
    "branch": (
        ("branch_id", "Branch ID", OntologyPropertyDataType.INTEGER, True, False, {}, "ID cơ sở từ Java"),
        ("name", "Branch Name", OntologyPropertyDataType.STRING, True, False, {}, "Tên cơ sở"),
        ("address", "Address", OntologyPropertyDataType.STRING, True, False, {}, "Địa chỉ cơ sở"),
        ("hotline", "Hotline", OntologyPropertyDataType.STRING, False, False, {}, "Số hotline cơ sở"),
        ("opened_date", "Opened Date", OntologyPropertyDataType.DATE, False, False, {}, "Ngày khai trương"),
        ("status", "Status", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "BranchStatus"}, "Trạng thái hoạt động"),
    ),
    "person": (
        ("person_id", "Person ID", OntologyPropertyDataType.UUID, True, False, {}, "UUID nhân sự từ Java"),
        ("person_code", "Person Code", OntologyPropertyDataType.STRING, False, False, {}, "Mã nhân sự"),
        ("full_name", "Full Name", OntologyPropertyDataType.STRING, True, False, {}, "Họ và tên"),
        ("current_belt", "Current Belt", OntologyPropertyDataType.STRING, False, False, {"enum_ref": "Belt"}, "Đẳng cấp đai hiện tại"),
        ("status", "Status", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "PersonStatus"}, "Trạng thái hoạt động"),
    ),
    "position": (
        ("position_id", "Position ID", OntologyPropertyDataType.UUID, True, False, {}, "UUID chức vụ"),
        ("code", "Code", OntologyPropertyDataType.STRING, True, False, {}, "Mã chức vụ"),
        ("name", "Name", OntologyPropertyDataType.STRING, True, False, {}, "Tên chức vụ"),
        ("description", "Description", OntologyPropertyDataType.STRING, False, False, {}, "Mô tả chức vụ"),
        ("active", "Active", OntologyPropertyDataType.BOOLEAN, True, False, {}, "Đang kích hoạt"),
    ),
    "course": (
        ("course_id", "Course ID", OntologyPropertyDataType.UUID, True, False, {}, "UUID lớp học"),
        ("name", "Course Name", OntologyPropertyDataType.STRING, True, False, {}, "Tên lớp học"),
        ("level", "Level", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "ScheduleLevel"}, "Trình độ lớp"),
        ("capacity", "Capacity", OntologyPropertyDataType.INTEGER, True, False, {"min": 1}, "Sĩ số tối đa"),
        ("status", "Status", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "CourseStatus"}, "Trạng thái lớp"),
    ),
    "class_schedule": (
        ("schedule_id", "Schedule ID", OntologyPropertyDataType.UUID, True, False, {}, "UUID khung giờ"),
        ("weekday", "Weekday", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "Weekday"}, "Thứ trong tuần"),
        ("location", "Location", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "ScheduleLocation"}, "Phòng/khu vực tập"),
        ("start_time", "Start Time", OntologyPropertyDataType.STRING, True, False, {}, "Giờ bắt đầu"),
        ("end_time", "End Time", OntologyPropertyDataType.STRING, True, False, {}, "Giờ kết thúc"),
        ("status", "Status", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "ScheduleStatus"}, "Trạng thái lịch"),
    ),
    "course_schedule": (
        ("course_schedule_id", "Course Schedule ID", OntologyPropertyDataType.UUID, True, False, {}, "UUID lịch khóa học"),
        ("start_date", "Start Date", OntologyPropertyDataType.DATE, False, False, {}, "Ngày bắt đầu"),
        ("end_date", "End Date", OntologyPropertyDataType.DATE, False, False, {}, "Ngày kết thúc"),
        ("session_type", "Session Type", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "SessionType"}, "Loại buổi học"),
        ("status", "Status", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "ScheduleStatus"}, "Trạng thái"),
    ),
    "training_plan": (
        ("training_plan_id", "Training Plan ID", OntologyPropertyDataType.UUID, True, False, {}, "UUID gói đào tạo"),
        ("name", "Plan Name", OntologyPropertyDataType.STRING, True, False, {}, "Tên gói đào tạo"),
        ("level", "Level", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "ScheduleLevel"}, "Cấp độ"),
        ("duration_months", "Duration Months", OntologyPropertyDataType.INTEGER, True, False, {"min": 1}, "Số tháng"),
        ("included_session_count", "Included Sessions", OntologyPropertyDataType.INTEGER, True, False, {"min": 0}, "Tổng số buổi"),
        ("base_price", "Base Price", OntologyPropertyDataType.FLOAT, True, False, {"min": 0}, "Giá gốc"),
        ("final_price", "Final Price", OntologyPropertyDataType.FLOAT, True, False, {"min": 0}, "Giá bán"),
        ("status", "Status", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "TrainingPlanStatus"}, "Trạng thái gói"),
    ),
    "training_plan_addon": (
        ("training_plan_addon_id", "Addon ID", OntologyPropertyDataType.UUID, True, False, {}, "UUID gói bổ sung"),
        ("name", "Addon Name", OntologyPropertyDataType.STRING, True, False, {}, "Tên phụ phí/gói bổ sung"),
        ("extra_sessions_per_week", "Extra Sessions/Week", OntologyPropertyDataType.INTEGER, True, False, {"min": 0}, "Số buổi thêm mỗi tuần"),
        ("price", "Price", OntologyPropertyDataType.FLOAT, True, False, {"min": 0}, "Đơn giá"),
        ("status", "Status", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "AddonStatus"}, "Trạng thái"),
    ),
    "training_plan_requirement": (
        ("training_plan_requirement_id", "Requirement ID", OntologyPropertyDataType.UUID, True, False, {}, "UUID yêu cầu gói"),
        ("session_type", "Session Type", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "SessionType"}, "Loại buổi học"),
        ("sessions_per_week", "Sessions/Week", OntologyPropertyDataType.INTEGER, True, False, {"min": 1}, "Số buổi mỗi tuần"),
    ),
    "training_plan_access_rule": (
        ("training_plan_access_rule_id", "Access Rule ID", OntologyPropertyDataType.UUID, True, False, {}, "UUID quy tắc truy cập"),
        ("session_type", "Session Type", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "SessionType"}, "Loại buổi học"),
    ),
    "belt_exam": (
        ("belt_exam_id", "Belt Exam ID", OntologyPropertyDataType.UUID, True, False, {}, "UUID kỳ thi lên đai"),
        ("from_belt", "From Belt", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "Belt"}, "Đai hiện tại"),
        ("target_belt", "Target Belt", OntologyPropertyDataType.STRING, True, False, {"enum_ref": "Belt"}, "Đai mục tiêu"),
        ("year", "Year", OntologyPropertyDataType.INTEGER, False, False, {}, "Năm tổ chức"),
        ("quarter", "Quarter", OntologyPropertyDataType.INTEGER, False, False, {"min": 1, "max": 4}, "Quý tổ chức"),
        ("exam_date", "Exam Date", OntologyPropertyDataType.DATE, False, False, {}, "Ngày thi"),
        ("type", "Exam Type", OntologyPropertyDataType.STRING, False, False, {"enum_ref": "BeltExamType"}, "Hình thức kỳ thi"),
    ),
    "course_staff_assignment": (
        ("assignment_id", "Assignment ID", OntologyPropertyDataType.UUID, True, False, {}, "UUID phân công HLV"),
        ("role", "Staff Role", OntologyPropertyDataType.STRING, True, False, {}, "Vai trò phụ trách"),
        ("status", "Status", OntologyPropertyDataType.STRING, True, False, {}, "Trạng thái phân công"),
    ),
    "class_session": (
        ("session_id", "Session ID", OntologyPropertyDataType.UUID, True, False, {}, "UUID buổi học"),
        ("session_date", "Session Date", OntologyPropertyDataType.DATE, True, False, {}, "Ngày diễn ra buổi học"),
        ("start_time", "Start Time", OntologyPropertyDataType.STRING, True, False, {}, "Giờ bắt đầu"),
        ("end_time", "End Time", OntologyPropertyDataType.STRING, True, False, {}, "Giờ kết thúc"),
        ("status", "Status", OntologyPropertyDataType.STRING, True, False, {}, "Trạng thái buổi học"),
    ),
}

LAYER_1_RELATIONSHIPS: tuple[tuple[str, str, str, RelationshipCardinality, str], ...] = (
    ("branch", "has_class_schedule", "class_schedule", RelationshipCardinality.ONE_TO_MANY, "Cơ sở sở hữu khung giờ học"),
    ("person", "holds_position", "position", RelationshipCardinality.MANY_TO_ONE, "Nhân sự giữ chức vụ"),
    ("person", "assigned_to_course_schedule", "course_schedule", RelationshipCardinality.MANY_TO_MANY, "HLV phụ trách lịch khóa học (CourseStaffAssignment)"),
    ("course", "has_course_schedule", "course_schedule", RelationshipCardinality.ONE_TO_MANY, "Khóa học có lịch vận hành"),
    ("course_schedule", "uses_class_schedule", "class_schedule", RelationshipCardinality.MANY_TO_ONE, "Lịch khóa học dùng khung giờ"),
    ("training_plan", "has_addon", "training_plan_addon", RelationshipCardinality.ONE_TO_MANY, "Gói đào tạo có gói bổ sung"),
    ("training_plan", "has_requirement", "training_plan_requirement", RelationshipCardinality.ONE_TO_MANY, "Gói đào tạo có yêu cầu số buổi"),
    ("training_plan", "has_access_rule", "training_plan_access_rule", RelationshipCardinality.ONE_TO_MANY, "Gói đào tạo có quy tắc truy cập lớp"),
    ("training_plan_access_rule", "grants_access_to_course", "course", RelationshipCardinality.MANY_TO_ONE, "Quy tắc cho phép tham gia lớp học"),
    ("course_staff_assignment", "assigns_person", "person", RelationshipCardinality.MANY_TO_ONE, "Phân công nhân sự/HLV"),
    ("course_staff_assignment", "assigned_for_course_schedule", "course_schedule", RelationshipCardinality.MANY_TO_ONE, "Phân công cho lịch khóa học"),
    ("class_session", "belongs_to_course_schedule", "course_schedule", RelationshipCardinality.MANY_TO_ONE, "Buổi học thuộc lịch khóa học"),
)

LAYER_1_ALIASES: dict[str, tuple[str, ...]] = {
    "branch": ("co so", "chi nhanh", "dojang", "dia diem"),
    "person": ("hlv", "huan luyen vien", "coach", "giao vien", "vo su"),
    "course": ("lop hoc", "khoa hoc", "class"),
    "class_schedule": ("lich tap", "ca hoc", "khung gio"),
    "training_plan": ("goi hoc", "goi dao tao", "goi hoc phi"),
    "training_plan_addon": ("phu phi", "vo phuc", "dung cu", "goi bo sung"),
    "belt_exam": ("ky thi len dai", "thi thang cap"),
}

__all__ = [
    "LAYER_1_ALIASES",
    "LAYER_1_ENTITY_TYPES",
    "LAYER_1_LAYER",
    "LAYER_1_OWNERSHIP",
    "LAYER_1_PROPERTIES",
    "LAYER_1_RELATIONSHIPS",
    "LAYER_1_SYNC_MODE",
    "get_layer1_entity_governance",
    "get_layer1_property_governance",
    "get_layer1_relationship_governance",
]
