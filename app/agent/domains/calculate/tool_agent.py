"""Calculation Tool Agent definition."""

from pathlib import Path

from google.adk.agents import Agent

from app.agent.adk.model_factory import create_model
from app.agent.tools.calculation import (
    add,
    divide,
    multiply,
    subtract,
)

PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "calculate"
    / "calculation_agent_tool.md"
)

CALCULATION_TOOLS = [add, subtract, multiply, divide]


def create_calculation_tool_agent() -> Agent:
    """Khởi tạo Tool Agent cho domain calculation.

    Agent này được gắn trực tiếp 4 function tools: add, subtract, multiply, divide.
    Thực hiện gọi tool để tính toán kết quả thay vì trả lời người dùng.
    """
    return Agent(
        name="calculation_tool_agent",
        model=create_model(),
        description=(
            "Phân tích biểu thức và trực tiếp gọi công cụ tính toán số học "
            "(cộng, trừ, nhân, chia). Không trả lời trực tiếp người dùng."
        ),
        instruction=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=CALCULATION_TOOLS,
    )


__all__ = ["create_calculation_tool_agent", "CALCULATION_TOOLS"]
