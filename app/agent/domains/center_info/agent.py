"""Center Info Agent definition for general center info, coaches, facilities, and contact."""

from google.adk.agents import Agent

from app.agent.domains.skill_agent_factory import create_skill_domain_agent
from app.agent.tools.center_info_tools import (
    search_center_facilities,
    search_center_knowledge,
    search_center_locations,
    search_coach_information,
    search_contact_information,
    search_opening_hours,
)

CENTER_INFO_SKILLS = (
    "center-information",
    "coach-information",
    "facilities-and-contact",
)

CENTER_INFO_INSTRUCTION = """
Bạn là Center Info Agent — chuyên viên cung cấp thông tin chung về trung tâm Taekwondo.

Nhiệm vụ chính:
- Giới thiệu lịch sử, thông tin trung tâm, địa chỉ các cơ sở và thời gian hoạt động.
- Giới thiệu đội ngũ huấn luyện viên.
- Tư vấn cơ sở vật chất phòng tập và thông tin liên hệ chính thức.

Nguyên tắc thực thi:
- Luôn nạp đúng skill phù hợp (`center-information`, `coach-information`, `facilities-and-contact`) trước khi trả lời.
- Chỉ cung cấp địa chỉ, giờ mở cửa, thông tin huấn luyện viên và kênh liên hệ từ kết quả tra cứu.
""".strip()


def create_center_info_agent() -> Agent:
    """Khởi tạo Center Info Agent với SkillToolset chuyên biệt về thông tin trung tâm."""
    return create_skill_domain_agent(
        name="CENTER_INFO_AGENT",
        description="Tư vấn thông tin chung về trung tâm, địa chỉ cơ sở, giờ mở cửa, huấn luyện viên, cơ sở vật chất và liên hệ.",
        instruction=CENTER_INFO_INSTRUCTION,
        skill_codes=CENTER_INFO_SKILLS,
        additional_tools=[
            search_center_knowledge,
            search_center_locations,
            search_opening_hours,
            search_coach_information,
            search_center_facilities,
            search_contact_information,
        ],
    )


__all__ = ["CENTER_INFO_SKILLS", "create_center_info_agent"]
