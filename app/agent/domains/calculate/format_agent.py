"""Calculation format agent definition."""

from pathlib import Path

from google.adk.agents import Agent

from app.agent.adk.model_factory import create_model

PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "calculate"
    / "calculation_format_agent.md"
)


def create_calculation_format_agent() -> Agent:
    """Khởi tạo Agent định dạng kết quả tính toán."""
    return Agent(
        name="calculation_format_agent",
        model=create_model(),
        description="Trình bày kết quả tính toán đã được xác thực cho người dùng.",
        instruction=PROMPT_PATH.read_text(encoding="utf-8"),
    )


__all__ = ["create_calculation_format_agent"]
