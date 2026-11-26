"""Các công cụ tra cứu lớp học và chương trình đào tạo cho Training Agent."""

from typing import Any

from app.agent.tools.java_catalog_tools import (
    JavaCatalogError,
    calendar_window,
    filter_by_query,
    get_resource,
    list_resource,
    tool_error,
    tool_result,
)


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


def search_training_knowledge(query: str) -> dict[str, Any]:
    """Tra cứu kiến thức chuyên môn Taekwondo (đòn thế, bài quyền, luật thi đấu).

    Args:
        query: Câu hỏi về kỹ thuật, bài quyền, lý thuyết hoặc thuật ngữ Taekwondo.
    """
    raise NotImplementedError("Chức năng tìm kiếm kiến thức Taekwondo chưa được triển khai!")


def search_training_curriculum(query: str) -> dict[str, Any]:
    """Tra cứu cấu trúc chương trình và lịch học theo gói từ Java.

    Args:
        query: Từ khóa về lộ trình giảng dạy, nội dung chương trình học.
    """
    try:
        catalog = filter_by_query(
            get_resource("/training-plans/registration-catalog"), query
        )
        return tool_result(
            query,
            {"registrationCatalog": catalog},
            "API cung cấp cấp độ, thời lượng, số buổi và các loại buổi học; nội dung giáo trình chi tiết vẫn cần tri thức đã kiểm chứng.",
        )
    except JavaCatalogError as exc:
        return tool_error(query, exc)


def search_training_benefits(query: str) -> dict[str, Any]:
    """Tra cứu lợi ích thể chất, tinh thần và kỷ luật của việc tập luyện Taekwondo.

    Args:
        query: Câu hỏi về tác dụng và lợi ích khi cho trẻ hoặc người lớn tập võ.
    """
    raise NotImplementedError("Chức năng tìm kiếm lợi ích tập võ chưa được triển khai!")


def search_belt_system(query: str) -> dict[str, Any]:
    """Tra cứu hệ thống cấp đai, ý nghĩa màu đai và lộ trình thăng cấp trong Taekwondo.

    Args:
        query: Câu hỏi về thứ tự cấp đai, đai màu và đai đen.
    """
    raise NotImplementedError("Chức năng tìm kiếm hệ thống cấp đai chưa được triển khai!")


def search_exam_policy(query: str) -> dict[str, Any]:
    """Tra cứu quy định, điều kiện, lịch thi và nội dung thi lên đai.

    Args:
        query: Câu hỏi về kỳ thi thăng đai và tiêu chuẩn đánh giá.
    """
    raise NotImplementedError("Chức năng tìm kiếm quy định thi lên đai chưa được triển khai!")


def search_training_policy(query: str) -> dict[str, Any]:
    """Tra cứu nội quy võ đường, quy định nghỉ học, xin phép và học bù.

    Args:
        query: Câu hỏi về nội quy lớp học, nghỉ phép hoặc lịch học bù.
    """
    raise NotImplementedError("Chức năng tìm kiếm quy định tập luyện chưa được triển khai!")


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
