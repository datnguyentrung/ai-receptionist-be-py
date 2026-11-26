"""Các công cụ tra cứu và tính toán học phí cho Tuition Agent."""

from typing import Any

from app.agent.tools.java_catalog_tools import (
    JavaCatalogError,
    filter_by_query,
    get_resource,
    list_resource,
    tool_error,
    tool_result,
)


def search_tuition_policy(query: str) -> dict[str, Any]:
    """Tra cứu chính sách học phí chung của trung tâm.

    Args:
        query: Câu hỏi hoặc từ khóa về quy định học phí, bảo lưu, hoàn phí.
    """
    raise NotImplementedError("Chức năng tìm kiếm chính sách học phí chưa được triển khai!")


def search_tuition_packages(query: str) -> dict[str, Any]:
    """Tra cứu danh sách các gói học phí theo tháng, quý, năm hoặc theo lớp.

    Args:
        query: Từ khóa hoặc tên gói học phí cần tra cứu.
    """
    try:
        plans = filter_by_query(list_resource("/training-plans"), query)
        details = [get_resource(f"/training-plans/{plan['trainingPlanId']}") for plan in plans[:10] if plan.get("trainingPlanId")]
        return tool_result(query, {"trainingPlans": details or plans})
    except JavaCatalogError as exc:
        return tool_error(query, exc)


def search_additional_fees(query: str) -> dict[str, Any]:
    """Tra cứu chi phí võ phục, đai, bảo hộ và các dụng cụ tập luyện phát sinh.

    Args:
        query: Từ khóa về võ phục, dụng cụ tập luyện hoặc lệ phí bổ sung.
    """
    try:
        addons = filter_by_query(list_resource("/training-plan-addons"), query)
        details = [get_resource(f"/training-plan-addons/{addon['trainingPlanAddonId']}") for addon in addons[:10] if addon.get("trainingPlanAddonId")]
        return tool_result(query, {"trainingPlanAddons": details or addons})
    except JavaCatalogError as exc:
        return tool_error(query, exc)


def search_discount_policy(query: str) -> dict[str, Any]:
    """Tra cứu chính sách ưu đãi, học bổng, giảm giá theo nhóm hoặc gia đình.

    Args:
        query: Câu hỏi hoặc từ khóa về chương trình ưu đãi, khuyến mãi.
    """
    raise NotImplementedError("Chức năng tìm kiếm chính sách ưu đãi chưa được triển khai!")


def calculate_tuition(
    package_name: str,
    months: int = 1,
    discount_code: str = "",
    student_count: int = 1,
) -> dict[str, Any]:
    """Tính toán tổng học phí dự kiến theo gói học, thời gian, số học viên và ưu đãi.

    Args:
        package_name: Tên gói học phí hoặc loại lớp.
        months: Số tháng đăng ký học.
        discount_code: Mã ưu đãi hoặc loại ưu đãi áp dụng (tùy chọn).
        student_count: Số lượng học viên đăng ký cùng lúc.
    """
    raise NotImplementedError("Chức năng tính toán học phí chưa được triển khai!")


def search_payment_policy(query: str) -> dict[str, Any]:
    """Tra cứu phương thức thanh toán, chuyển khoản, tiền mặt và quy định đóng học phí.

    Args:
        query: Câu hỏi hoặc từ khóa về hình thức và thời hạn đóng học phí.
    """
    raise NotImplementedError("Chức năng tìm kiếm phương thức thanh toán chưa được triển khai!")


__all__ = [
    "calculate_tuition",
    "search_additional_fees",
    "search_discount_policy",
    "search_payment_policy",
    "search_tuition_packages",
    "search_tuition_policy",
]
