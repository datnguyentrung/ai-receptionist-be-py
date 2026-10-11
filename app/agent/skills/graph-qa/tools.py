"""Tool registration for the graph-qa skill."""

from app.agent.tools.graphrag_tools import (
    execute_read_cypher,
    hybrid_search,
    retrieve_taekwondo_document_history,
    retrieve_taekwondo_knowledge,
    vector_search,
)


def get_tools() -> list:
    return [
        execute_read_cypher,
        vector_search,
        hybrid_search,
        retrieve_taekwondo_knowledge,
        retrieve_taekwondo_document_history,
    ]


__all__ = ["get_tools"]
