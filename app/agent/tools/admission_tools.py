"""Các công cụ tra cứu và tư vấn tuyển sinh cho Admission Agent."""

from typing import Any

from app.agent.tools.java_catalog_tools import (
    JavaCatalogError,
    filter_by_query,
    get_resource,
    list_resource,
    tool_error,
    tool_result,
)


def search_admission_knowledge(query: str) -> dict[str, Any]:
    """Tra cứu điều kiện nhập học từ dữ liệu gói đào tạo Java.

    Args:
        query: Câu hỏi hoặc từ khóa về điều kiện nhập học, độ tuổi, hồ sơ tuyển sinh.
    """
    try:
        requirements = filter_by_query(list_resource("/training-plan-requirements"), query)
        return tool_result(query, {"trainingPlanRequirements": requirements})
    except JavaCatalogError as exc:
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
    try:
        catalog = filter_by_query(
            get_resource("/training-plans/registration-catalog"), query
        )
        return tool_result(
            query,
            {"studentAge": age, "registrationCatalog": catalog},
            "Danh mục gồm giá, cấp độ, số buổi và yêu cầu lịch của từng gói. "
            "Java chưa có điều kiện độ tuổi hay mục tiêu tập luyện, nên không được khẳng định một lớp phù hợp chỉ từ dữ liệu này.",
        )
    except JavaCatalogError as exc:
        return tool_error(query, exc)


def search_trial_policy(query: str) -> dict[str, Any]:
    """Tra cứu chính sách học thử dành cho học viên mới.

    Args:
        query: Câu hỏi hoặc từ khóa liên quan đến buổi học thử, chi phí và điều kiện.
    """
    raise NotImplementedError("Chức năng tìm kiếm chính sách học thử chưa được triển khai!")


def search_registration_process(query: str) -> dict[str, Any]:
    """Tra cứu các lựa chọn gói và lịch trong bước đăng ký từ Java.

    Args:
        query: Câu hỏi hoặc từ khóa về cách thức đăng ký học tại trung tâm.
    """
    try:
        catalog = filter_by_query(
            get_resource("/training-plans/registration-catalog"), query
        )
        return tool_result(
            query,
            {"registrationCatalog": catalog},
            "Dữ liệu API bao gồm gói, giá và yêu cầu lịch. Các bước hồ sơ hoặc chính sách đăng ký không có trong API này.",
        )
    except JavaCatalogError as exc:
        return tool_error(query, exc)


__all__ = [
    "recommend_class",
    "search_admission_knowledge",
    "search_class_knowledge",
    "search_registration_process",
    "search_trial_policy",
]
