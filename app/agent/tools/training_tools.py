"""Các công cụ tra cứu lớp học và chương trình đào tạo cho Training Agent."""

from typing import Any

from app.agent.tools.graphrag_tools import (
    retrieve_scope_knowledge,
    retrieve_scope_knowledge_sync,
)
from app.agent.tools.java_catalog_tools import (
    JavaCatalogError,
    calendar_window,
    filter_by_query,
    get_resource,
    list_resource,
    tool_error,
    tool_result,
)
from app.services.ontology.seeds.layer2_core import TOOL_SCOPE_MAP


def search_class_schedule(query: str) -> dict[str, Any]:
    """Tra cứu lịch tập luyện, ca học và thời lượng từng buổi của các lớp.

    Args:
        query: Từ khóa về lịch học, ngày tập, khung giờ hoặc tên lớp.
    """
    try:
        schedules = filter_by_query(list_resource("/class-schedules"), query)
        course_schedules = filter_by_query(list_resource("/course-schedules"), query)
        sessions = filter_by_query(list_resource("/class-sessions/calendar", calendar_window()), query)
        return tool_result(query, {"classSchedules": schedules, "courseSchedules": course_schedules, "calendarSessions": sessions})
    except JavaCatalogError as exc:
        return tool_error(query, exc)


def search_class_details(query: str) -> dict[str, Any]:
    """Tra cứu thông tin chi tiết về lớp học (sĩ số, trình độ, yêu cầu đầu vào).

    Args:
        query: Tên lớp hoặc câu hỏi chi tiết về lớp học.
    """
    try:
        courses = filter_by_query(list_resource("/courses"), query)
        details = [get_resource(f"/courses/{course['courseId']}") for course in courses[:10] if course.get("courseId")]
        return tool_result(query, {"courses": details or courses, "courseSchedules": filter_by_query(list_resource("/course-schedules"), query), "staffAssignments": filter_by_query(list_resource("/course-staff-assignments"), query)})
    except JavaCatalogError as exc:
        return tool_error(query, exc)


async def search_training_knowledge(query: str) -> dict[str, Any]:
    """Tra cứu kiến thức chuyên môn Taekwondo (đòn thế, bài quyền, luật thi đấu).

    Args:
        query: Câu hỏi về kỹ thuật, bài quyền, lý thuyết hoặc thuật ngữ Taekwondo.
    """
    return await retrieve_scope_knowledge(query, TOOL_SCOPE_MAP["search_training_knowledge"])


def search_training_curriculum(query: str) -> dict[str, Any]:
    """Tra cứu cấu trúc chương trình và lịch học theo gói từ Java kết hợp giáo trình chi tiết từ GraphRAG.

    Args:
        query: Từ khóa về lộ trình giảng dạy, nội dung chương trình học.
    """
    graph_context = retrieve_scope_knowledge_sync(
        query or "nội dung giáo trình chương trình học",
        TOOL_SCOPE_MAP["search_training_curriculum"],
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
            "Kết hợp cấu trúc gói/số buổi từ Java API và nội dung giáo trình chi tiết từ GraphRAG.",
        )
    except JavaCatalogError as exc:
        if graph_context.get("passages") or graph_context.get("facts"):
            return tool_result(
                query,
                {
                    "registrationCatalog": [],
                    "graphKnowledge": graph_context,
                },
                "Trả về nội dung giáo trình chi tiết từ GraphRAG (Java Catalog tạm thời không khả dụng).",
            )
        return tool_error(query, exc)


async def search_training_benefits(query: str) -> dict[str, Any]:
    """Tra cứu lợi ích thể chất, tinh thần và kỷ luật của việc tập luyện Taekwondo.

    Args:
        query: Câu hỏi về tác dụng và lợi ích khi cho trẻ hoặc người lớn tập võ.
    """
    return await retrieve_scope_knowledge(query, TOOL_SCOPE_MAP["search_training_benefits"])


async def search_belt_system(query: str) -> dict[str, Any]:
    """Tra cứu hệ thống cấp đai, ý nghĩa màu đai và lộ trình thăng cấp trong Taekwondo.

    Args:
        query: Câu hỏi về thứ tự cấp đai, đai màu và đai đen.
    """
    return await retrieve_scope_knowledge(query, TOOL_SCOPE_MAP["search_belt_system"])


async def search_exam_policy(query: str) -> dict[str, Any]:
    """Tra cứu quy định, điều kiện, lịch thi và nội dung thi lên đai.

    Args:
        query: Câu hỏi về kỳ thi thăng đai và tiêu chuẩn đánh giá.
    """
    return await retrieve_scope_knowledge(query, TOOL_SCOPE_MAP["search_exam_policy"])


async def search_training_policy(query: str) -> dict[str, Any]:
    """Tra cứu nội quy võ đường, quy định nghỉ học, xin phép và học bù.

    Args:
        query: Câu hỏi về nội quy lớp học, nghỉ phép hoặc lịch học bù.
    """
    return await retrieve_scope_knowledge(query, TOOL_SCOPE_MAP["search_training_policy"])


__all__ = [
    "search_belt_system",
    "search_class_details",
    "search_class_schedule",
    "search_exam_policy",
    "search_training_benefits",
    "search_training_curriculum",
    "search_training_knowledge",
    "search_training_policy",
]
