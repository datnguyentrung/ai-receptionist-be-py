"""Graph QA tool agent definition."""

from pathlib import Path

from google.adk.agents import Agent

from app.agent.adk.model_factory import create_model
from app.agent.tools.graphrag_tools import (
    execute_read_cypher,
    hybrid_search,
    retrieve_taekwondo_document_history,
    retrieve_taekwondo_knowledge,
    vector_search,
)
from app.agent.tools.schema_context_tools import (
    list_active_schema_scopes,
    load_active_schema_context,
)

PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "graph_qa"
    / "graph_qa_agent_tool.md"
)


def create_graph_qa_tool_agent() -> Agent:
    """Khởi tạo Agent điều phối công cụ truy xuất Graph QA."""
    return Agent(
        name="graph_qa_tool_agent",
        model=create_model(),
        description=(
            "Phân tích câu hỏi và gọi công cụ truy xuất tri thức "
            "(Cypher, Vector, Hybrid search, Schema Context). "
            "Không trả lời trực tiếp người dùng."
        ),
        instruction=PROMPT_PATH.read_text(encoding="utf-8"),
        tools=[
            execute_read_cypher,
            vector_search,
            hybrid_search,
            retrieve_taekwondo_knowledge,
            retrieve_taekwondo_document_history,
            list_active_schema_scopes,
            load_active_schema_context,
        ],
    )


__all__ = ["create_graph_qa_tool_agent"]
