"""Graph QA keeps retrieval behind its deterministic adapter."""


def get_tools() -> list:
    """Do not expose GraphRAG internals to an LLM SkillToolset."""
    return []


__all__ = ["get_tools"]
