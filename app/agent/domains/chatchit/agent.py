"""Small-talk agent for messages outside the business domains."""

from google.adk.agents import Agent

from app.agent.adk.model_factory import create_model


CHATCHIT_INSTRUCTION = """
Bạn là trợ lý lễ tân thân thiện của võ đường Taekwondo Văn Quán.

Chỉ xử lý chào hỏi, cảm ơn, trò chuyện xã giao, lời chúc và phản hồi ngắn không
đòi hỏi tra cứu tri thức hay thực hiện nghiệp vụ. Trả lời tự nhiên, ngắn gọn,
và bằng ngôn ngữ của người dùng. Không tự bịa thông tin về võ đường, lịch học,
học phí hoặc Taekwondo. Nếu người dùng chuyển sang một nhu cầu cụ thể, mời họ
nêu câu hỏi đó để hệ thống hỗ trợ.
""".strip()


def create_chatchit_agent() -> Agent:
    """Create the no-tools conversational agent selected by Clef."""
    return Agent(
        name="CHATCHIT_AGENT",
        model=create_model(),
        description="Trò chuyện xã giao, chào hỏi và phản hồi lịch sự ngoài các luồng nghiệp vụ.",
        instruction=CHATCHIT_INSTRUCTION,
        tools=[],
    )


__all__ = ["create_chatchit_agent"]
