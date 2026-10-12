"""Graph QA format agent definition."""

from pathlib import Path

from google.adk.agents import Agent

from app.agent.adk.model_factory import create_model

PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "graph_qa"
    / "graph_qa_format_agent.md"
)


def create_graph_qa_format_agent() -> Agent:
    """Khởi tạo Agent định dạng câu trả lời Graph QA."""
    return Agent(
        name="graph_qa_format_agent",
        model=create_model(),
        description="Định dạng câu trả lời tri thức Taekwondo dựa trên bằng chứng đã truy xuất.",
        instruction=PROMPT_PATH.read_text(encoding="utf-8"),
    )


__all__ = ["create_graph_qa_format_agent"]
