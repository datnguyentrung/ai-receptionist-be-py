"""ADK tools for source-grounded Taekwondo GraphRAG retrieval."""

import asyncio
import logging
import re
from typing import Any, cast

logger = logging.getLogger(__name__)

from google.adk.tools import ToolContext
from neo4j import RoutingControl

from app.core.config import settings
from app.core.ingestion_runtime import get_service_container
from app.core.tenant_context import get_current_tenant_context, require_tenant_id
from app.services.graphrag.cypher_guard import (
    normalize_neo4j_value,
    validate_read_cypher,
)
from app.services.graphrag.retrieval import (
    INSUFFICIENT_EVIDENCE,
    EvidenceBundle,
    GraphQaRequest,
    GraphRAGRetriever,
    _public_passage,  # pyright: ignore[reportPrivateUsage]
)
from app.services.ingestion.engine.operations import normalize_document_key


async def execute_read_cypher(
    cypher: str,
    parameters: dict[str, Any] | None = None,
    limit: int = 50,
    tool_context: ToolContext | None = None,
) -> dict[str, Any]:
    """Disabled for freeform queries; use parameterized GraphRAG retrieval tools instead."""
    del cypher, parameters, limit, tool_context
    return {
        "success": False,
        "errorCode": "CYPHER_FREEFORM_DISABLED",
        "message": "Freeform Cypher is disabled for multi-tenant isolation; use parameterized GraphRAG retrieval tools.",
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

    try:
        resolved_tenant = require_tenant_id()
    except (ValueError, PermissionError) as exc:
        return {
            "success": False,
            "errorCode": "TENANT_REQUIRED",
            "message": str(exc),
        }

    effective_top_k = min(max(1, int(top_k)), 50)
    container = await get_service_container()
    query_vector = await container.embedding_provider.embed_query(cleaned_query)

    # Over-fetch from shared vector index before tenantId post-filtering so other tenants don't crowd out results
    vector_fetch_limit = max(effective_top_k * 5, 50)
    chunk_hits, fact_hits = await asyncio.gather(
        container.rag_graph_store.search_chunk_vector(
            query_vector,
            vector_fetch_limit,
            tenant_id=resolved_tenant,
        ),
        container.rag_graph_store.search_fact_vector(
            query_vector,
            vector_fetch_limit,
            tenant_id=resolved_tenant,
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

    chunk_hits.sort(key=lambda hit: -float(hit.get("score", 0.0)))
    selected = chunk_hits[:effective_top_k]

    top_score = float(selected[0]["score"]) if selected else 0.0
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
    resolved_tenant = require_tenant_id()
    container = await get_service_container()
    retriever = GraphRAGRetriever(
        container.rag_graph_store,
        container.embedding_provider,
        min_cosine_score=settings.RAG_MIN_COSINE_SCORE,
        candidate_limit=settings.RAG_CANDIDATE_LIMIT,
        context_max_chars=settings.RAG_CONTEXT_MAX_CHARS,
        default_top_k=settings.RAG_DEFAULT_TOP_K,
        retrieval_timeout_seconds=settings.RAG_RETRIEVAL_TIMEOUT_SECONDS,
        path_timeout_seconds=settings.RAG_PATH_TIMEOUT_SECONDS,
    )
    result = await retriever.retrieve(
        GraphQaRequest(question=question, tenant_id=resolved_tenant, scope_key=scope_key),
    )
    return result.model_dump(mode="json")


async def retrieve_graph_qa_evidence(
    request: GraphQaRequest,
) -> EvidenceBundle:
    """Retrieve one safe evidence bundle for the deterministic Graph QA adapter.

    This is Python-only glue, not an ADK function tool. The Graph QA agent never
    exposes the underlying vector, Cypher, schema, or hybrid paths to an LLM.
    """
    container = await get_service_container()
    retriever = GraphRAGRetriever(
        container.rag_graph_store,
        container.embedding_provider,
        min_cosine_score=settings.RAG_MIN_COSINE_SCORE,
        candidate_limit=settings.RAG_CANDIDATE_LIMIT,
        context_max_chars=settings.RAG_CONTEXT_MAX_CHARS,
        default_top_k=settings.RAG_DEFAULT_TOP_K,
        retrieval_timeout_seconds=settings.RAG_RETRIEVAL_TIMEOUT_SECONDS,
        path_timeout_seconds=settings.RAG_PATH_TIMEOUT_SECONDS,
    )
    return await retriever.retrieve(request)


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
    resolved_tenant = require_tenant_id()
    container = await get_service_container()
    normalized = normalize_document_key(document_key)
    rows = await container.rag_graph_store.document_history(
        normalized, tenant_id=resolved_tenant
    )
    return {"documentKey": normalized, "tenantId": resolved_tenant, "assertions": rows}


async def retrieve_scope_knowledge(
    question: str,
    scope_key: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Retrieve grounded GraphRAG evidence for a specific Layer 2 Knowledge Scope."""
    try:
        resolved_tenant = require_tenant_id(tenant_id)
        bundle = await retrieve_graph_qa_evidence(
            GraphQaRequest(
                question=question.strip() or scope_key,
                scope_key=scope_key,
                tenant_id=resolved_tenant,
            )
        )
        logger.info(
            "GRAPHRAG_RETRIEVE | tenantId=%s | scopeKey=%s | query=%r | status=%s | passages=%d | facts=%d | abstention=%s",
            resolved_tenant,
            scope_key,
            question.strip() or scope_key,
            bundle.status.value,
            len(bundle.passages),
            len(bundle.facts),
            bundle.abstention_message,
        )
        return {
            "source": "graphrag",
            "scopeKey": scope_key,
            "status": bundle.status.value,
            "passages": [p.model_dump(mode="json") for p in bundle.passages],
            "facts": [f.model_dump(mode="json") for f in bundle.facts],
            "abstentionMessage": bundle.abstention_message,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "GRAPHRAG_RETRIEVE_FAILED | tenantId=%s | scopeKey=%s | query=%r | error=%s",
            tenant_id,
            scope_key,
            question,
            exc,
        )
        return {
            "source": "graphrag",
            "scopeKey": scope_key,
            "status": "UNAVAILABLE",
            "passages": [],
            "facts": [],
            "abstentionMessage": str(exc),
        }


def retrieve_scope_knowledge_sync(
    question: str,
    scope_key: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Thread-safe synchronous bridge for Hybrid tools that also call sync Java APIs."""
    from concurrent.futures import ThreadPoolExecutor

    ctx = get_current_tenant_context()
    effective_tenant_id = tenant_id or (ctx.tenant_id if ctx is not None else None)
    coro = retrieve_scope_knowledge(question, scope_key, tenant_id=effective_tenant_id)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


__all__ = [
    "retrieve_graph_qa_evidence",
    "retrieve_scope_knowledge",
    "retrieve_scope_knowledge_sync",
]
