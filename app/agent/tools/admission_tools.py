"""Các công cụ tra cứu và tư vấn tuyển sinh cho Admission Agent."""

from typing import Any

from app.agent.tools.graphrag_tools import (
    retrieve_scope_knowledge,
    retrieve_scope_knowledge_sync,
)
from app.agent.tools.java_catalog_tools import (
    JavaCatalogError,
    filter_by_query,
    get_resource,
    list_resource,
    tool_error,
    tool_result,
)
from app.services.ontology.seeds.layer2_core import TOOL_SCOPE_MAP


def search_admission_knowledge(query: str) -> dict[str, Any]:
    """Tra cứu điều kiện nhập học, độ tuổi tiếp nhận và quy định tuyển sinh từ GraphRAG kết hợp yêu cầu gói tập từ Java.

    Args:
        query: Câu hỏi hoặc từ khóa về điều kiện nhập học, độ tuổi, hồ sơ tuyển sinh.
    """
    graph_context = retrieve_scope_knowledge_sync(
        query or "điều kiện nhập học độ tuổi tiếp nhận trẻ em hồ sơ tuyển sinh",
        "admission_onboarding",
    )
    try:
        requirements = filter_by_query(list_resource("/training-plan-requirements"), query)
        return tool_result(
            query,
            {
                "trainingPlanRequirements": requirements,
                "graphKnowledge": graph_context,
            },
            "Kết hợp điều kiện nhập học/độ tuổi tiếp nhận từ GraphRAG và yêu cầu số buổi gói đào tạo từ Java API.",
        )
    except JavaCatalogError as exc:
        if graph_context.get("passages") or graph_context.get("facts"):
            return tool_result(
                query,
                {
                    "trainingPlanRequirements": [],
                    "graphKnowledge": graph_context,
                },
                "Trả về điều kiện nhập học và độ tuổi tiếp nhận từ GraphRAG (Java Catalog tạm thời không khả dụng).",
            )
        return tool_error(query, exc)


def search_class_knowledge(query: str) -> dict[str, Any]:
    """Tra cứu thông tin tổng quan các lớp học hiện có tại trung tâm.

    Args:
        query: Từ khóa hoặc mô tả về lớp học cần tra cứu.
    """
    try:
        courses = filter_by_query(list_resource("/courses"), query)
        details = [get_resource(f"/courses/{course['courseId']}") for course in courses[:10] if course.get("courseId")]
        return tool_result(query, {"courses": details or courses})
    except JavaCatalogError as exc:
        return tool_error(query, exc)


def recommend_class(
    age: int,
    goal: str = "",
    schedule_preference: str = "",
) -> dict[str, Any]:
    """Gợi ý lớp học Taekwondo phù hợp theo độ tuổi, mục tiêu và lịch rảnh.

    Args:
        age: Độ tuổi của học viên.
        goal: Mục tiêu tập luyện (ví dụ: rèn thể lực, tự vệ, thi đấu, phong trào).
        schedule_preference: Khung giờ hoặc ngày trong tuần mong muốn (tùy chọn).
    """
    query = " ".join(value for value in (goal, schedule_preference) if value).strip()
    graph_context = retrieve_scope_knowledge_sync(
        f"gợi ý lớp học độ tuổi {age} mục tiêu {goal} lịch {schedule_preference}".strip(),
        TOOL_SCOPE_MAP["recommend_class"],
    )
    try:
        catalog = filter_by_query(
            get_resource("/training-plans/registration-catalog"), query
        )
        return tool_result(
            query,
            {
                "studentAge": age,
                "registrationCatalog": catalog,
                "graphKnowledge": graph_context,
            },
            "Kết hợp dữ liệu gói/lịch từ Java API và hồ sơ độ tuổi/mục tiêu từ GraphRAG.",
        )
    except JavaCatalogError as exc:
        if graph_context.get("passages") or graph_context.get("facts"):
            return tool_result(
                query,
                {
                    "studentAge": age,
                    "registrationCatalog": [],
                    "graphKnowledge": graph_context,
                },
                "Trả về hồ sơ độ tuổi/mục tiêu từ GraphRAG (Java Catalog tạm thời không khả dụng).",
            )
        return tool_error(query, exc)


async def search_trial_policy(query: str) -> dict[str, Any]:
    """Tra cứu chính sách học thử dành cho học viên mới.

    Args:
        query: Câu hỏi hoặc từ khóa liên quan đến buổi học thử, chi phí và điều kiện.
    """
    return await retrieve_scope_knowledge(query, TOOL_SCOPE_MAP["search_trial_policy"])


def search_registration_process(query: str) -> dict[str, Any]:
    """Tra cứu các lựa chọn gói và lịch trong bước đăng ký từ Java kết hợp quy trình từ GraphRAG.

    Args:
        query: Câu hỏi hoặc từ khóa về cách thức đăng ký học tại trung tâm.
    """
    graph_context = retrieve_scope_knowledge_sync(
        query or "quy trình đăng ký nhập học",
        TOOL_SCOPE_MAP["search_registration_process"],
    )
    try:
        catalog = filter_by_query(
            get_resource("/training-plans/registration-catalog"), query
        )
        return tool_result(
            query,
            {
                "registrationCatalog": catalog,
                "graphKnowledge": graph_context,
            },
            "Kết hợp danh mục gói/lịch từ Java API và quy trình/hồ sơ đăng ký từ GraphRAG.",
        )
    except JavaCatalogError as exc:
        if graph_context.get("passages") or graph_context.get("facts"):
            return tool_result(
                query,
                {
                    "registrationCatalog": [],
                    "graphKnowledge": graph_context,
                },
                "Trả về quy trình/hồ sơ đăng ký từ GraphRAG (Java Catalog tạm thời không khả dụng).",
            )
        return tool_error(query, exc)


__all__ = [
    "recommend_class",
    "search_admission_knowledge",
    "search_class_knowledge",
    "search_registration_process",
    "search_trial_policy",
]
