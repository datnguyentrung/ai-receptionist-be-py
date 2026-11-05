"""Graph QA SequentialAgent definition."""

from google.adk.agents import SequentialAgent

from app.agent.domains.graph_qa.format_agent import (
    create_graph_qa_format_agent,
)
from app.agent.domains.graph_qa.retrieval_agent import (
    GraphQaRetrievalAdapter,
)


def create_graph_qa_agent() -> SequentialAgent:
    """Run deterministic hybrid retrieval, then format its grounded evidence."""
    return SequentialAgent(
        name="GRAPH_QA_AGENT",
        description=(
            "Trả lời các câu hỏi về Taekwondo, võ thuật, kỹ thuật, bài quyền, "
            "điều lệ và quy định võ đường thông qua truy xuất Knowledge Graph và định dạng câu trả lời."
        ),
        sub_agents=[
            GraphQaRetrievalAdapter(),
            create_graph_qa_format_agent(),
        ],
    )


__all__ = ["create_graph_qa_agent"]
