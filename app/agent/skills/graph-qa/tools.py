"""Tool registration for the graph-qa skill."""

from app.agent.tools.graphrag_tools import retrieve_taekwondo_knowledge


def get_tools() -> list:
    return [retrieve_taekwondo_knowledge]


__all__ = ["get_tools"]
