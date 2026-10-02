"""Hybrid Neo4j retrieval with provenance, reranking, and abstention."""

import asyncio
import logging
import re
import time
from typing import Any

from app.services.graphrag.embeddings import GeminiEmbeddingProvider, cosine_similarity
from app.services.ingestion.graph_store import Neo4jIngestionStore

logger = logging.getLogger(__name__)
INSUFFICIENT_EVIDENCE = (
    "Không có đủ bằng chứng trong kho tri thức để trả lời câu hỏi này."
)


class GraphRAGRetriever:
    def __init__(
        self,
        graph_store: Neo4jIngestionStore,
        embedding_provider: GeminiEmbeddingProvider,
        *,
        min_cosine_score: float,
        candidate_limit: int,
        context_max_chars: int,
    ) -> None:
        self._graph_store = graph_store
        self._embedding_provider = embedding_provider
        self._min_cosine_score = min_cosine_score
        self._candidate_limit = max(1, candidate_limit)
        self._context_max_chars = max(1000, context_max_chars)

    async def retrieve(
        self,
        question: str,
        *,
        scope_key: str | None = None,
        top_k: int = 10,
    ) -> dict[str, Any]:
        query = question.strip()
        if not query:
            raise ValueError("question must not be empty")
        top_k = min(max(1, int(top_k)), 50)
        started = time.perf_counter()
        query_vector = await self._embedding_provider.embed_query(query)
        lucene_query = _lucene_query(query)

        chunk_vector, chunk_fulltext, entity_hits, fact_hits = await asyncio.gather(
            self._graph_store.search_chunk_vector(query_vector, self._candidate_limit),
            self._graph_store.search_chunk_fulltext(
                lucene_query, self._candidate_limit
            ),
            self._graph_store.search_entity_fulltext(
                lucene_query, self._candidate_limit
            ),
            self._graph_store.search_fact_vector(query_vector, self._candidate_limit),
        )
        entity_keys = [
            str(item["stableKey"]) for item in entity_hits if item.get("stableKey")
        ]
        fact_chunk_ids = sorted(
            {
                str(chunk_id)
                for fact in fact_hits
                for chunk_id in fact.get("chunkIds", [])
            }
        )
        entity_chunks, fact_chunks = await asyncio.gather(
            self._graph_store.chunks_for_entities(entity_keys, self._candidate_limit),
            self._graph_store.chunks_by_ids(fact_chunk_ids),
        )

        paths = {
            "chunkVector": _filter_scope(chunk_vector, scope_key),
            "chunkFulltext": _filter_scope(chunk_fulltext, scope_key),
            "entityTraversal": _filter_scope(entity_chunks, scope_key),
            "factSupport": _filter_scope(fact_chunks, scope_key),
        }
        fused = _reciprocal_rank_fusion(paths)
        ranked: list[dict[str, Any]] = []
        for item in fused.values():
            embedding = item.get("embedding") or []
            cosine = cosine_similarity(query_vector, embedding)
            item["cosineScore"] = cosine
            item["score"] = cosine
            ranked.append(item)
        ranked.sort(
            key=lambda item: (-float(item["cosineScore"]), -float(item["rrfScore"]))
        )
        selected = _within_context_budget(ranked[:top_k], self._context_max_chars)
        top_score = float(selected[0]["cosineScore"]) if selected else 0.0
        sufficient = bool(selected and top_score >= self._min_cosine_score)
        passages = [_public_passage(item) for item in selected] if sufficient else []
        selected_chunk_ids = {
            str(item.get("chunkId")) for item in selected if item.get("chunkId")
        }
        public_facts = [
            {
                "factId": item.get("factId"),
                "statement": item.get("statement"),
                "factType": item.get("factType"),
                "score": item.get("score"),
                "chunkIds": sorted(
                    selected_chunk_ids.intersection(
                        str(chunk_id) for chunk_id in item.get("chunkIds", [])
                    )
                ),
            }
            for item in fact_hits[:top_k]
            if selected_chunk_ids.intersection(
                str(chunk_id) for chunk_id in item.get("chunkIds", [])
            )
        ]
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        trace = {
            "pathCounts": {name: len(items) for name, items in paths.items()},
            "fusedCandidates": len(ranked),
            "returnedPassages": len(passages),
            "topCosineScore": top_score,
            "minimumCosineScore": self._min_cosine_score,
            "latencyMs": elapsed_ms,
            "citationIds": [item["citationId"] for item in passages],
        }
        logger.info(
            "GraphRAG retrieval | paths=%s | fused=%s | returned=%s | topScore=%.4f | "
            "abstained=%s | latencyMs=%.2f",
            trace["pathCounts"],
            trace["fusedCandidates"],
            trace["returnedPassages"],
            top_score,
            not sufficient,
            elapsed_ms,
        )
        return {
            "success": True,
            "strategy": "neo4j_hybrid_graphrag",
            "query": query,
            "scopeKey": scope_key,
            "sufficientEvidence": sufficient,
            "abstentionMessage": None if sufficient else INSUFFICIENT_EVIDENCE,
            "passages": passages,
            "facts": public_facts if sufficient else [],
            "retrievalTrace": trace,
        }


def _reciprocal_rank_fusion(
    paths: dict[str, list[dict[str, Any]]], *, k: int = 60
) -> dict[str, dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}
    for path_name, items in paths.items():
        for rank, item in enumerate(items, start=1):
            chunk_id = item.get("chunkId")
            if not chunk_id:
                continue
            key = f"{item.get('versionId', '')}:{chunk_id}"
            target = fused.setdefault(key, {**item, "rrfScore": 0.0, "paths": []})
            target["rrfScore"] += 1.0 / (k + rank)
            target["paths"].append(path_name)
            if not target.get("embedding") and item.get("embedding"):
                target["embedding"] = item["embedding"]
    return fused


def _filter_scope(
    items: list[dict[str, Any]], scope_key: str | None
) -> list[dict[str, Any]]:
    if not scope_key:
        return items
    normalized = scope_key.strip().casefold()
    return [
        item
        for item in items
        if normalized in {
            str(value).casefold()
            for value in (item.get("scopeKeys") or [item.get("scopeKey")])
            if value
        }
    ]


def _within_context_budget(
    items: list[dict[str, Any]], max_chars: int
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    used = 0
    for item in items:
        text = str(item.get("text") or "")
        if result and used + len(text) > max_chars:
            continue
        result.append(item)
        used += len(text)
    return result


def _public_passage(item: dict[str, Any]) -> dict[str, Any]:
    document_name = str(item.get("documentName") or item.get("documentId") or "source")
    location = item.get("section") or (
        f"trang {item['pageStart']}"
        if item.get("pageStart")
        else item.get("sourceAnchor")
    )
    citation = f"{document_name} — {location}" if location else document_name
    return {
        "citationId": f"{item.get('versionId')}:{item.get('chunkId')}",
        "citation": citation,
        "content": item.get("text"),
        "quote": item.get("text"),
        "score": round(float(item.get("cosineScore", 0.0)), 6),
        "rrfScore": round(float(item.get("rrfScore", 0.0)), 6),
        "retrievalPaths": sorted(set(item.get("paths", []))),
        "source": {
            "documentId": item.get("documentId"),
            "versionId": item.get("versionId"),
            "chunkId": item.get("chunkId"),
            "documentName": document_name,
            "chunkIndex": item.get("chunkIndex"),
            "section": item.get("section"),
            "pageStart": item.get("pageStart"),
            "pageEnd": item.get("pageEnd"),
            "sourceAnchor": item.get("sourceAnchor"),
            "scopeKey": item.get("scopeKey"),
        },
    }


def _lucene_query(query: str) -> str:
    tokens = re.findall(r"\w+", query, flags=re.UNICODE)
    if not tokens:
        return '"' + query.replace('"', '\\"') + '"'
    return " OR ".join(f'"{token}"' for token in tokens[:20])


__all__ = ["INSUFFICIENT_EVIDENCE", "GraphRAGRetriever"]
