"""Admission Agent definition for parent onboarding and enrollment consultation."""

from google.adk.agents import Agent

from app.agent.domains.skill_agent_factory import create_skill_domain_agent
from app.agent.tools.admission_tools import (
    recommend_class,
    search_admission_knowledge,
    search_class_knowledge,
    search_registration_process,
    search_trial_policy,
)

ADMISSION_SKILLS = (
    "admission-consultation",
    "class-recommendation",
    "trial-consultation",
)

ADMISSION_INSTRUCTION = """
Bạn là Admission Agent — chuyên viên tư vấn tuyển sinh cho phụ huynh và học viên mới của trung tâm Taekwondo.

Nhiệm vụ chính:
- Tư vấn điều kiện nhập học, độ tuổi và hồ sơ cần thiết.
- Gợi ý lớp học phù hợp theo độ tuổi, mục tiêu và lịch rảnh.
- Tư vấn chính sách học thử và quy trình đăng ký nhập học.

Nguyên tắc thực thi:
- Luôn kiểm tra và nạp đúng skill phù hợp (`admission-consultation`, `class-recommendation`, `trial-consultation`) trước khi trả lời.
- Chỉ dùng thông tin từ công cụ tra cứu, không tự bịa chính sách hay điều kiện nhập học.
- Trả lời bằng tiếng Việt thân thiện, rõ ràng, dễ hiểu cho phụ huynh.
""".strip()


def create_admission_agent() -> Agent:
    """Khởi tạo Admission Agent với SkillToolset chuyên biệt về tuyển sinh."""
    return create_skill_domain_agent(
        name="ADMISSION_AGENT",
        description="Tư vấn phụ huynh mới, điều kiện nhập học, gợi ý lớp học, học thử và quy trình đăng ký.",
        instruction=ADMISSION_INSTRUCTION,
        skill_codes=ADMISSION_SKILLS,
        additional_tools=[
            search_admission_knowledge,
            search_class_knowledge,
            recommend_class,
            search_trial_policy,
            search_registration_process,
        ],
    )


__all__ = ["ADMISSION_SKILLS", "create_admission_agent"]
