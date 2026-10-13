"""Calculation SequentialAgent definition with 2 sub-agents: Tool Agent & Format Agent."""

from google.adk.agents import SequentialAgent

from app.agent.domains.calculate.format_agent import (
    create_calculation_format_agent,
)
from app.agent.domains.calculate.tool_agent import (
    CALCULATION_TOOLS,
    create_calculation_tool_agent,
)


def create_calculation_agent() -> SequentialAgent:
    """Khởi tạo domain CALCULATION_AGENT tuần tự gồm đúng 2 sub-agents:

    1. Calculation Tool Agent: Gọi công cụ tính toán số học.
    2. Calculation Format Agent: Định dạng và trình bày kết quả cho người dùng.
    """
    return SequentialAgent(
        name="CALCULATION_AGENT",
        description=(
            "Thực hiện các phép tính cộng, trừ, nhân, chia "
            "thông qua công cụ tính toán và trình bày kết quả lịch sự, chính xác."
        ),
        sub_agents=[
            create_calculation_tool_agent(),
            create_calculation_format_agent(),
        ],
    )


__all__ = [
    "CALCULATION_TOOLS",
    "create_calculation_agent",
]
