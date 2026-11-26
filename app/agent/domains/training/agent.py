"""Training Agent definition for classes, curriculum, belt progression, and policies."""

from google.adk.agents import Agent

from app.agent.domains.skill_agent_factory import create_skill_domain_agent
from app.agent.tools.training_tools import (
    search_belt_system,
    search_class_details,
    search_class_schedule,
    search_exam_policy,
    search_training_benefits,
    search_training_curriculum,
    search_training_knowledge,
    search_training_policy,
)

TRAINING_SKILLS = (
    "class-information",
    "training-knowledge",
    "belt-progression",
    "training-policy",
)

TRAINING_INSTRUCTION = """
Bạn là Training Agent — chuyên viên tư vấn lớp học, chương trình đào tạo và kiến thức Taekwondo.

Nhiệm vụ chính:
- Tư vấn lịch học, thời lượng và thông tin chi tiết các lớp học.
- Tư vấn kiến thức Taekwondo, chương trình đào tạo và lợi ích của việc tập võ.
- Tư vấn lộ trình phát triển cấp đai và quy định thi lên đai.
- Tư vấn nội quy võ đường, quy định nghỉ học và học bù.

Nguyên tắc thực thi:
- Luôn nạp đúng skill phù hợp (`class-information`, `training-knowledge`, `belt-progression`, `training-policy`) trước khi trả lời.
- Bám sát dữ liệu từ công cụ tra cứu, không tự suy diễn lịch học hay quy chế thi đấu/lên đai.
""".strip()


def create_training_agent() -> Agent:
    """Khởi tạo Training Agent với SkillToolset chuyên biệt về đào tạo và lớp học."""
    return create_skill_domain_agent(
        name="TRAINING_AGENT",
        description="Tư vấn lịch học, chi tiết lớp học, kiến thức Taekwondo, chương trình đào tạo, cấp đai và nội quy.",
        instruction=TRAINING_INSTRUCTION,
        skill_codes=TRAINING_SKILLS,
        additional_tools=[
            search_class_schedule,
            search_class_details,
            search_training_knowledge,
            search_training_curriculum,
            search_training_benefits,
            search_belt_system,
            search_exam_policy,
            search_training_policy,
        ],
    )


__all__ = ["TRAINING_SKILLS", "create_training_agent"]
