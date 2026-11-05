"""Compatibility factory for Graph QA's deterministic retrieval adapter.

The historical name is retained temporarily for imports, but this factory no
longer creates an LLM agent or exposes any retrieval function tools.
"""

from app.agent.domains.graph_qa.retrieval_agent import GraphQaRetrievalAdapter


def create_graph_qa_tool_agent() -> GraphQaRetrievalAdapter:
    """Return deterministic retrieval; kept for backwards-compatible imports."""
    return GraphQaRetrievalAdapter()


__all__ = ["create_graph_qa_tool_agent"]
