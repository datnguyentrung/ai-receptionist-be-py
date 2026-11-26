"""Các công cụ tra cứu thông tin trung tâm cho Center Info Agent và Chatchit Agent."""

from typing import Any

from app.agent.tools.java_catalog_tools import (
    JavaCatalogError,
    filter_by_query,
    get_resource,
    list_resource,
    tool_error,
    tool_result,
)


def search_center_knowledge(query: str) -> dict[str, Any]:
    """Tra cứu thông tin giới thiệu chung, lịch sử hình thành và tầm nhìn của trung tâm.

    Args:
        query: Câu hỏi hoặc từ khóa về võ đường, lịch sử, sứ mệnh trung tâm.
    """
    raise NotImplementedError("Chức năng tìm kiếm thông tin trung tâm chưa được triển khai!")


def search_center_locations(query: str) -> dict[str, Any]:
    """Tra cứu địa chỉ các cơ sở, phòng tập và hướng dẫn đường đi.

    Args:
        query: Từ khóa về địa điểm, khu vực hoặc tên cơ sở của trung tâm.
    """
    try:
        branches = filter_by_query(list_resource("/branches"), query)
        details = [get_resource(f"/branches/{branch['branchId']}") for branch in branches[:10] if branch.get("branchId")]
        return tool_result(query, {"branches": details or branches})
    except JavaCatalogError as exc:
        return tool_error(query, exc)


def search_opening_hours(query: str) -> dict[str, Any]:
    """Tra cứu thời gian hoạt động, giờ mở cửa của trung tâm theo ngày trong tuần.

    Args:
        query: Câu hỏi về giờ làm việc, giờ mở cửa hoặc ngày nghỉ lễ.
    """
    raise NotImplementedError("Chức năng tìm kiếm thời gian hoạt động chưa được triển khai!")


def search_coach_information(query: str) -> dict[str, Any]:
    """Tra cứu thông tin đội ngũ huấn luyện viên, đẳng cấp và kinh nghiệm giảng dạy.

    Args:
        query: Tên huấn luyện viên hoặc câu hỏi về đội ngũ giảng dạy.
    """
    try:
        people = filter_by_query(list_resource("/persons"), query)
        positions = filter_by_query(list_resource("/positions"), query)
        assignments = filter_by_query(list_resource("/course-staff-assignments"), query)
        return tool_result(query, {"persons": people, "positions": positions, "courseStaffAssignments": assignments})
    except JavaCatalogError as exc:
        return tool_error(query, exc)


def search_center_facilities(query: str) -> dict[str, Any]:
    """Tra cứu thông tin cơ sở vật chất, thảm tập, trang thiết bị và tiện ích phòng tập.

    Args:
        query: Câu hỏi về trang thiết bị, phòng tập hoặc tiện ích của trung tâm.
    """
    raise NotImplementedError("Chức năng tìm kiếm thông tin cơ sở vật chất chưa được triển khai!")


def search_contact_information(query: str) -> dict[str, Any]:
    """Tra cứu thông tin liên hệ, hotline, fanpage, Zalo và kênh hỗ trợ nhân viên.

    Args:
        query: Câu hỏi về số điện thoại, email, kênh liên hệ hoặc gặp tư vấn viên.
    """
    raise NotImplementedError("Chức năng tìm kiếm thông tin liên hệ chưa được triển khai!")


__all__ = [
    "search_center_facilities",
    "search_center_knowledge",
    "search_center_locations",
    "search_coach_information",
    "search_contact_information",
    "search_opening_hours",
]
