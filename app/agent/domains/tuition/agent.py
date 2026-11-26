"""Tuition Agent definition for fees, packages, discounts, and payment consultation."""

from google.adk.agents import Agent

from app.agent.domains.skill_agent_factory import create_skill_domain_agent
from app.agent.tools.tuition_tools import (
    calculate_tuition,
    search_additional_fees,
    search_discount_policy,
    search_payment_policy,
    search_tuition_packages,
    search_tuition_policy,
)

TUITION_SKILLS = (
    "tuition-consultation",
    "discount-consultation",
    "payment-consultation",
)

TUITION_INSTRUCTION = """
Bạn là Tuition Agent — chuyên viên tư vấn học phí, ưu đãi và phương thức thanh toán của trung tâm Taekwondo.

Nhiệm vụ chính:
- Tư vấn chính sách học phí, các gói học phí và chi phí võ phục, dụng cụ.
- Tư vấn chương trình ưu đãi, khuyến mãi và tính toán học phí dự kiến.
- Tư vấn phương thức và quy định đóng học phí.

Nguyên tắc thực thi:
- Luôn nạp đúng skill phù hợp (`tuition-consultation`, `discount-consultation`, `payment-consultation`) trước khi tư vấn.
- Không tự tính nhẩm hoặc tự bịa mức giá, ưu đãi hay số tài khoản thanh toán; luôn gọi công cụ tương ứng.
- Trình bày chi phí minh bạch, tách bạch học phí và chi phí dụng cụ.
""".strip()


def create_tuition_agent() -> Agent:
    """Khởi tạo Tuition Agent với SkillToolset chuyên biệt về học phí và ưu đãi."""
    return create_skill_domain_agent(
        name="TUITION_AGENT",
        description="Tư vấn học phí, gói học, chi phí võ phục/dụng cụ, ưu đãi, tính học phí và thanh toán.",
        instruction=TUITION_INSTRUCTION,
        skill_codes=TUITION_SKILLS,
        additional_tools=[
            search_tuition_policy,
            search_tuition_packages,
            search_additional_fees,
            search_discount_policy,
            calculate_tuition,
            search_payment_policy,
        ],
    )


__all__ = ["TUITION_SKILLS", "create_tuition_agent"]
