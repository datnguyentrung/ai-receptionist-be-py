"""Calculation tool agent definition."""

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


def create_calculation_tool_agent() -> Agent:
    """Khởi tạo Agent điều phối công cụ tính toán."""
    return Agent(
        name="calculation_tool_agent",
        model=create_model(),
        description=(
            "Phân tích biểu thức và gọi công cụ cộng, trừ, "
            "nhân, chia. Không trả lời trực tiếp."
        ),
        instruction=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=[
            add,
            subtract,
            multiply,
            divide,
        ],
    )


__all__ = ["create_calculation_tool_agent"]
