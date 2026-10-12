"""Graph QA SequentialAgent definition."""

from google.adk.agents import SequentialAgent

from app.agent.domains.graph_qa.format_agent import (
    create_graph_qa_format_agent,
)
from app.agent.domains.graph_qa.tool_agent import (
    create_graph_qa_tool_agent,
)


def create_graph_qa_agent() -> SequentialAgent:
    """Khởi tạo domain GRAPH_QA_AGENT tuần tự gồm tool agent và format agent."""
    return SequentialAgent(
        name="GRAPH_QA_AGENT",
        description=(
            "Trả lời các câu hỏi về Taekwondo, võ thuật, kỹ thuật, bài quyền, "
            "điều lệ và quy định võ đường thông qua truy xuất Knowledge Graph và định dạng câu trả lời."
        ),
        sub_agents=[
            create_graph_qa_tool_agent(),
            create_graph_qa_format_agent(),
        ],
    )


__all__ = ["create_graph_qa_agent"]
