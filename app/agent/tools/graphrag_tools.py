"""ADK tools for source-grounded Taekwondo GraphRAG retrieval."""

from typing import Any

from google.adk.tools import ToolContext

from app.core.config import settings
from app.core.ingestion_runtime import get_service_container
from app.services.graphrag.retrieval import GraphRAGRetriever


async def retrieve_taekwondo_knowledge(
    question: str,
    tool_context: ToolContext,
    scope_key: str | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    """Retrieve grounded Taekwondo passages and facts from Neo4j.

    Use this exactly once before answering a factual question about the
    Taekwondo knowledge base. The result explicitly indicates whether evidence
    is sufficient and includes citation-ready source metadata.
    """

    container = await get_service_container()
    retriever = GraphRAGRetriever(
        container.graph_store,
        container.embedding_provider,
        min_cosine_score=settings.RAG_MIN_COSINE_SCORE,
        candidate_limit=settings.RAG_CANDIDATE_LIMIT,
        context_max_chars=settings.RAG_CONTEXT_MAX_CHARS,
    )
    result = await retriever.retrieve(
        question,
        scope_key=scope_key,
        top_k=top_k or settings.RAG_DEFAULT_TOP_K,
    )
    tool_context.state["last_retrieval_trace"] = result.get("retrievalTrace", {})
    return result


__all__ = ["retrieve_taekwondo_knowledge"]
