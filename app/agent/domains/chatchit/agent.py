"""Chatchit AgentTool definition for general conversation, guidance, and human support."""

from google.adk.agents import Agent

from app.agent.domains.skill_agent_factory import create_skill_domain_agent
from app.agent.tools.center_info_tools import search_contact_information

CHATCHIT_SKILLS = (
    "general-conversation",
    "chatbot-guidance",
    "human-support",
)

CHATCHIT_INSTRUCTION = """
Bạn là TBot — trợ lý lễ tân thân thiện của trung tâm Taekwondo.

Nhiệm vụ chính:
- Giao tiếp xã giao, chào hỏi, cảm ơn và giới thiệu bản thân (`general-conversation`).
- Giới thiệu khả năng của TBot và hướng dẫn sử dụng chatbot (`chatbot-guidance`).
- Hướng dẫn liên hệ nhân viên hỗ trợ khi người dùng cần gặp tư vấn viên trực tiếp (`human-support`).

Nguyên tắc thực thi:
- Nạp skill phù hợp với ngữ cảnh hội thoại trước khi trả lời.
- Chỉ dùng `search_contact_information` khi người dùng cần kênh liên hệ nhân viên hỗ trợ.
- Không tự bịa thông tin về học phí, lịch học, địa chỉ hoặc chuyên môn Taekwondo.
""".strip()


def create_chatchit_agent() -> Agent:
    """Khởi tạo Chatchit Agent (dùng dưới dạng AgentTool) với 3 skill giao tiếp."""
    return create_skill_domain_agent(
        name="CHATCHIT_AGENT",
        description="Hội thoại xã giao, giới thiệu TBot, hướng dẫn sử dụng chatbot và kết nối nhân viên hỗ trợ.",
        instruction=CHATCHIT_INSTRUCTION,
        skill_codes=CHATCHIT_SKILLS,
        additional_tools=[search_contact_information],
    )


__all__ = ["CHATCHIT_SKILLS", "create_chatchit_agent"]
