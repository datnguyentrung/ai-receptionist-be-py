"""ADK tools for source-grounded Taekwondo GraphRAG retrieval."""

import asyncio
from typing import Any, cast

from google.adk.tools import ToolContext
from neo4j import RoutingControl

from app.core.config import settings
from app.core.ingestion_runtime import get_service_container
from app.services.graphrag.cypher_guard import (
    normalize_neo4j_value,
    validate_read_cypher,
)
from app.services.graphrag.embeddings import cosine_similarity
from app.services.graphrag.retrieval import (
    INSUFFICIENT_EVIDENCE,
    GraphRAGRetriever,
    _public_passage,
)
from app.services.ingestion.engine.operations import normalize_document_key


async def execute_read_cypher(
    cypher: str,
    parameters: dict[str, Any] | None = None,
    limit: int = 50,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Execute a parameterized read-only Cypher query against the knowledge graph.

    Use when exact node labels, relationship types, and property keys are known
    from the loaded schema context. Always prefer parameterized $variables.
    Must include a LIMIT clause (default safety cap is 50, max 200).

    Args:
        cypher: Read-only Cypher statement. Must not contain write keywords or semicolons.
                Use neo4jPropertyKey names from schema, not namespace forms (p.pskg:...).
        parameters: Dict of parameter bindings for $variables in cypher.
        limit: Safety cap for maximum rows returned (default 50, max 200).
        tool_context: Injected by ADK runtime to store retrieval traces.

    Returns:
        {success: True, rowCount: int, rows: list[dict]} or
        {success: False, errorCode: str, message: str}
    """
    try:
        validate_read_cypher(cypher)
    except ValueError as exc:
        return {
            "success": False,
            "errorCode": "INVALID_CYPHER",
            "message": str(exc),
        }

    effective_limit = min(max(1, int(limit)), 200)
    params = dict(parameters or {})

    container = await get_service_container()
    try:
        async with container.neo4j_driver.session(
            database=settings.NEO4J_DATABASE,
            default_access_mode=RoutingControl.READ,
        ) as session:
            result = await session.run(cast(Any, cypher), **params)
            rows = [
                {k: normalize_neo4j_value(v) for k, v in record.items()}
                async for record in result
            ]

        capped_rows = rows[:effective_limit]
        if tool_context is not None:
            tool_context.state["last_cypher_query"] = {
                "cypher": cypher,
                "parameters": params,
                "rowCount": len(capped_rows),
            }

        return {
            "success": True,
            "rowCount": len(capped_rows),
            "rows": capped_rows,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "errorCode": "NEO4J_ERROR",
            "message": str(exc),
        }


async def vector_search(
    query: str,
    scope_key: str | None = None,
    top_k: int = 10,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Semantic vector search on knowledge chunks and facts.

    Use when the question is conceptual or exact entity names are not yet known.
    Embeds the query, queries Neo4j vector indexes (chunks + facts), and reranks
    by cosine similarity.

    Args:
        query: Natural language question or search phrase.
        scope_key: Optional scope key to filter results (from list_active_schema_scopes).
        top_k: Number of top passages to return (default 10, max 50).
        tool_context: Injected by ADK runtime to store retrieval traces.

    Returns:
        {success: True, strategy: "vector_search", passages: [...], facts: [...],
         sufficientEvidence: bool, topCosineScore: float}
    """
    cleaned_query = query.strip()
    if not cleaned_query:
        return {
            "success": False,
            "errorCode": "EMPTY_QUERY",
            "message": "Query must not be empty.",
        }

    effective_top_k = min(max(1, int(top_k)), 50)
    container = await get_service_container()
    query_vector = await container.embedding_provider.embed_query(cleaned_query)

    candidate_multiplier = 2
    chunk_hits, fact_hits = await asyncio.gather(
        container.rag_graph_store.search_chunk_vector(
            query_vector, effective_top_k * candidate_multiplier
        ),
        container.rag_graph_store.search_fact_vector(
            query_vector, effective_top_k * candidate_multiplier
        ),
    )

    # Lọc theo scope_key nếu có
    if scope_key:
        norm_scope = scope_key.strip().casefold()
        chunk_hits = [
            h
            for h in chunk_hits
            if norm_scope
            in {
                str(v).casefold()
                for v in (h.get("scopeKeys") or [h.get("scopeKey")])
                if v
            }
        ]

    # Rerank chunk hits bằng cosine_similarity
    for hit in chunk_hits:
        emb = hit.get("embedding") or []
        hit["cosineScore"] = cosine_similarity(query_vector, emb)
    chunk_hits.sort(key=lambda h: -float(h.get("cosineScore", 0.0)))
    selected = chunk_hits[:effective_top_k]

    top_score = float(selected[0]["cosineScore"]) if selected else 0.0
    sufficient = bool(selected and top_score >= settings.RAG_MIN_COSINE_SCORE)

    result = {
        "success": True,
        "strategy": "vector_search",
        "query": cleaned_query,
        "scopeKey": scope_key,
        "sufficientEvidence": sufficient,
        "topCosineScore": round(top_score, 4),
        "passages": [_public_passage(h) for h in selected] if sufficient else [],
        "facts": fact_hits[:effective_top_k] if sufficient else [],
        "abstentionMessage": None if sufficient else INSUFFICIENT_EVIDENCE,
    }

    if tool_context is not None:
        tool_context.state["last_vector_trace"] = {
            "query": cleaned_query,
            "topScore": top_score,
            "sufficientEvidence": sufficient,
        }

    return result


async def retrieve_taekwondo_knowledge(
    question: str,
    tool_context: ToolContext,
    scope_key: str | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    """Retrieve grounded Taekwondo passages and facts using hybrid 4-path retrieval.

    Combines chunk vector + fulltext + entity traversal + fact support with
    Reciprocal Rank Fusion (RRF) and context length budgeting.
    """
    container = await get_service_container()
    retriever = GraphRAGRetriever(
        container.rag_graph_store,
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


async def hybrid_search(
    question: str,
    tool_context: ToolContext,
    scope_key: str | None = None,
    top_k: int = 10,
) -> dict[str, Any]:
    """Retrieve grounded Taekwondo passages and facts using hybrid 4-path retrieval.

    Combines chunk vector + fulltext + entity traversal + fact support with
    Reciprocal Rank Fusion (RRF) and context length budgeting.
    """
    return await retrieve_taekwondo_knowledge(
        question, tool_context, scope_key=scope_key, top_k=top_k
    )


async def retrieve_taekwondo_document_history(document_key: str) -> dict[str, Any]:
    """Return every published version of one document with original evidence."""
    container = await get_service_container()
    normalized = normalize_document_key(document_key)
    rows = await container.rag_graph_store.document_history(normalized)
    return {"documentKey": normalized, "assertions": rows}


__all__ = [
    "execute_read_cypher",
    "hybrid_search",
    "retrieve_taekwondo_document_history",
    "retrieve_taekwondo_knowledge",
    "vector_search",
]
