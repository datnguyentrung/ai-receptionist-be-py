"""Calculation SequentialAgent definition."""

from google.adk.agents import SequentialAgent

from app.agent.domains.calculate.format_agent import (
    create_calculation_format_agent,
)
from app.agent.domains.calculate.tool_agent import (
    create_calculation_tool_agent,
)


def create_calculation_agent() -> SequentialAgent:
    """Khởi tạo domain CALCULATION_AGENT tuần tự gồm tool agent và format agent."""
    return SequentialAgent(
        name="CALCULATION_AGENT",
        description=(
            "Thực hiện các phép tính cộng, trừ, nhân, chia "
            "theo quy trình gọi công cụ rồi trình bày kết quả."
        ),
        sub_agents=[
            create_calculation_tool_agent(),
            create_calculation_format_agent(),
        ],
    )


__all__ = ["create_calculation_agent"]
