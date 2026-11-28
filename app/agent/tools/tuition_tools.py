"""Các công cụ tra cứu và tính toán học phí cho Tuition Agent."""

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
from app.services.knowledge.tuition_calculator import calculate_tuition_deterministic
from app.services.ontology.seeds.layer2_core import TOOL_SCOPE_MAP


async def search_tuition_policy(query: str) -> dict[str, Any]:
    """Tra cứu chính sách học phí chung của trung tâm.

    Args:
        query: Câu hỏi hoặc từ khóa về quy định học phí, bảo lưu, hoàn phí.
    """
    return await retrieve_scope_knowledge(query, TOOL_SCOPE_MAP["search_tuition_policy"])


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


async def search_discount_policy(query: str) -> dict[str, Any]:
    """Tra cứu chính sách ưu đãi, học bổng, giảm giá theo nhóm hoặc gia đình.

    Args:
        query: Câu hỏi hoặc từ khóa về chương trình ưu đãi, khuyến mãi.
    """
    return await retrieve_scope_knowledge(query, TOOL_SCOPE_MAP["search_discount_policy"])


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
    try:
        plans = filter_by_query(list_resource("/training-plans"), package_name)
        plan_details = [
            get_resource(f"/training-plans/{plan['trainingPlanId']}")
            for plan in plans[:10]
            if plan.get("trainingPlanId")
        ]
        addons = list_resource("/training-plan-addons")
        resolved_plans = plan_details or plans
        calculation = None
        if resolved_plans:
            first_plan = resolved_plans[0]
            raw_price = first_plan.get("finalPrice", first_plan.get("basePrice"))
            if isinstance(raw_price, (int, float)):
                calculation = calculate_tuition_deterministic(
                    package_name=str(first_plan.get("name") or package_name),
                    unit_price=float(raw_price),
                    months=months,
                    student_count=student_count,
                    discount_code=discount_code,
                    plan_id=str(first_plan.get("trainingPlanId") or "") or None,
                ).model_dump(mode="json")

        graph_context = retrieve_scope_knowledge_sync(
            f"ưu đãi giảm giá học phí {package_name} {months} tháng {student_count} học viên {discount_code}".strip(),
            TOOL_SCOPE_MAP["calculate_tuition"],
        )
        return tool_result(
            package_name,
            {
                "packageName": package_name,
                "months": months,
                "discountCode": discount_code,
                "studentCount": student_count,
                "trainingPlans": resolved_plans,
                "trainingPlanAddons": addons,
                "calculation": calculation,
                "graphKnowledge": graph_context,
            },
            "Kết hợp đơn giá từ Java API, tính toán deterministic và quy tắc ưu đãi từ GraphRAG.",
        )
    except JavaCatalogError as exc:
        return tool_error(package_name, exc)


async def search_payment_policy(query: str) -> dict[str, Any]:
    """Tra cứu phương thức thanh toán, chuyển khoản, tiền mặt và quy định đóng học phí.

    Args:
        query: Câu hỏi hoặc từ khóa về hình thức và thời hạn đóng học phí.
    """
    return await retrieve_scope_knowledge(query, TOOL_SCOPE_MAP["search_payment_policy"])


__all__ = [
    "calculate_tuition",
    "search_additional_fees",
    "search_discount_policy",
    "search_payment_policy",
    "search_tuition_packages",
    "search_tuition_policy",
]
