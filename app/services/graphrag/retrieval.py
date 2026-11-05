"""Hybrid Neo4j retrieval with provenance, reranking, and abstention."""

import asyncio
import logging
import re
import time
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.services.graphrag.embeddings import GeminiEmbeddingProvider, cosine_similarity
from app.services.graphrag.graph_store import Neo4jGraphStore

logger = logging.getLogger(__name__)
INSUFFICIENT_EVIDENCE = (
    "Không có đủ bằng chứng trong kho tri thức để trả lời câu hỏi này."
)


class EvidenceStatus(StrEnum):
    READY = "READY"
    INSUFFICIENT = "INSUFFICIENT"
    UNAVAILABLE = "UNAVAILABLE"


class GraphQaRequest(BaseModel):
    """Trusted request accepted by the Graph QA retrieval seam."""

    question: str = Field(min_length=1, max_length=2000)
    scope_key: str | None = None


class GroundedPassage(BaseModel):
    citation: str
    content: str


class GroundedFact(BaseModel):
    statement: str
    citation_ids: list[str] = Field(default_factory=list)
    currentness_note: str | None = None


class EvidenceBundle(BaseModel):
    """Safe, grounded retrieval result for the formatter.

    Retrieval diagnostics, vector scores, raw Neo4j values, and implementation
    strategy remain internal to the retrieval module.
    """

    status: EvidenceStatus
    passages: list[GroundedPassage] = Field(default_factory=list)
    facts: list[GroundedFact] = Field(default_factory=list)
    abstention_message: str | None = None


class GraphEvidenceRetriever(Protocol):
    async def retrieve(self, request: GraphQaRequest) -> EvidenceBundle: ...


class GraphRAGRetriever:
    def __init__(
        self,
        graph_store: Neo4jGraphStore,
        embedding_provider: GeminiEmbeddingProvider,
        *,
        min_cosine_score: float,
        candidate_limit: int,
        context_max_chars: int,
        default_top_k: int = 10,
        retrieval_timeout_seconds: float = 12.0,
        path_timeout_seconds: float = 5.0,
    ) -> None:
        self._graph_store = graph_store
        self._embedding_provider = embedding_provider
        self._min_cosine_score = min_cosine_score
        self._candidate_limit = max(1, candidate_limit)
        self._context_max_chars = max(1000, context_max_chars)
        self._default_top_k = min(max(1, default_top_k), 50)
        self._retrieval_timeout_seconds = max(0.1, retrieval_timeout_seconds)
        self._path_timeout_seconds = max(0.1, path_timeout_seconds)

    async def retrieve(
        self,
        request: GraphQaRequest,
    ) -> EvidenceBundle:
        """Run one bounded hybrid retrieval operation for one user question."""
        try:
            return await asyncio.wait_for(
                self._retrieve(request), timeout=self._retrieval_timeout_seconds
            )
        except asyncio.TimeoutError:
            logger.warning(
                "GraphRAG retrieval timed out after %.2fs",
                self._retrieval_timeout_seconds,
            )
            return EvidenceBundle(status=EvidenceStatus.UNAVAILABLE)
        except Exception:
            logger.exception("GraphRAG retrieval unavailable")
            return EvidenceBundle(status=EvidenceStatus.UNAVAILABLE)

    async def _retrieve(self, request: GraphQaRequest) -> EvidenceBundle:
        query = request.question.strip()
        if not query:
            return EvidenceBundle(
                status=EvidenceStatus.INSUFFICIENT,
                abstention_message=INSUFFICIENT_EVIDENCE,
            )
        scope_key = request.scope_key
        top_k = self._default_top_k
        started = time.perf_counter()
        lucene_query = _lucene_query(query)

        # Start lexical retrieval while the embedding request is in flight.
        embedding_task = asyncio.create_task(
            self._embedding_provider.embed_query(query)
        )
        fulltext_task = asyncio.create_task(
            self._optional_path(
                "chunk_fulltext",
                self._graph_store.search_chunk_fulltext(
                    lucene_query, self._candidate_limit
                ),
            )
        )
        entity_task = asyncio.create_task(
            self._optional_path(
                "entity_fulltext",
                self._graph_store.search_entity_fulltext(
                    lucene_query, self._candidate_limit
                ),
            )
        )
        (
            (chunk_fulltext, fulltext_available),
            (entity_hits, entity_available),
        ) = await asyncio.gather(fulltext_task, entity_task)

        query_vector: list[float] | None
        try:
            query_vector = await asyncio.wait_for(
                embedding_task, timeout=self._path_timeout_seconds
            )
        except Exception:
            logger.warning("GraphRAG embedding path unavailable", exc_info=True)
            query_vector = None

        if query_vector:
            (
                (chunk_vector, vector_available),
                (fact_hits, fact_available),
            ) = await asyncio.gather(
                self._optional_path(
                    "chunk_vector",
                    self._graph_store.search_chunk_vector(
                        query_vector, self._candidate_limit
                    ),
                ),
                self._optional_path(
                    "fact_vector",
                    self._graph_store.search_fact_vector(
                        query_vector, self._candidate_limit
                    ),
                ),
            )
        else:
            chunk_vector, fact_hits = [], []
            vector_available = fact_available = False
        entity_keys = [
            str(item["stableKey"]) for item in entity_hits if item.get("stableKey")
        ]
        fact_chunk_ids = sorted(
            {
                str(chunk_id)
                for fact in fact_hits
                for chunk_id in fact.get("chunkIds") or []
            }
        )
        peer_chunk_ids = sorted(
            {
                str(chunk_id)
                for fact in fact_hits
                for peer in fact.get("conflictPeers") or []
                if peer.get("decision") in {"PENDING", "OTHER"}
                for chunk_id in peer.get("chunkIds") or []
            }
        )
        (entity_chunks, _), (fact_chunks, _), (peer_chunks, _) = await asyncio.gather(
            self._optional_path(
                "entity_traversal",
                self._graph_store.chunks_for_entities(
                    entity_keys, self._candidate_limit
                ),
            ),
            self._optional_path(
                "fact_support",
                self._graph_store.chunks_by_ids(
                    fact_chunk_ids, include_historical=False
                ),
            ),
            self._optional_path(
                "conflict_peers",
                self._graph_store.chunks_by_ids(
                    peer_chunk_ids, include_historical=True
                ),
            ),
        )

        if not any(
            (fulltext_available, entity_available, vector_available, fact_available)
        ):
            return EvidenceBundle(status=EvidenceStatus.UNAVAILABLE)

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
            cosine = cosine_similarity(query_vector, embedding) if query_vector else 0.0
            item["cosineScore"] = cosine
            ranked.append(item)
        ranked.sort(
            key=lambda item: (-float(item["rrfScore"]), -float(item["cosineScore"]))
        )
        selected = _within_context_budget(ranked[:top_k], self._context_max_chars)
        selected_ids = {str(item.get("chunkId")) for item in selected}
        peer_ids = {
            str(peer_chunk)
            for fact in fact_hits
            if selected_ids.intersection(str(cid) for cid in fact.get("chunkIds") or [])
            for peer in fact.get("conflictPeers") or []
            if peer.get("decision") in {"PENDING", "OTHER"}
            for peer_chunk in peer.get("chunkIds") or []
        }
        selected.extend(
            item
            for item in peer_chunks
            if str(item.get("chunkId")) in peer_ids
            and str(item.get("chunkId")) not in selected_ids
        )
        top_score = max(
            (float(item.get("cosineScore", 0.0)) for item in selected), default=0.0
        )
        has_lexical_evidence = any(
            {"chunkFulltext", "entityTraversal"}.intersection(item.get("paths", []))
            for item in selected
        )
        sufficient = bool(
            selected
            and (
                (
                    (vector_available or fact_available)
                    and top_score >= self._min_cosine_score
                )
                or (not (vector_available or fact_available) and has_lexical_evidence)
            )
        )
        passages = [_public_passage(item) for item in selected] if sufficient else []
        selected_chunk_ids = {
            str(item.get("chunkId")) for item in selected if item.get("chunkId")
        }
        citation_by_chunk = {
            str(item.get("chunkId")): f"{item.get('versionId')}:{item.get('chunkId')}"
            for item in selected
            if item.get("chunkId")
        }
        public_facts = [
            GroundedFact(
                statement=str(item.get("statement") or ""),
                citation_ids=sorted(
                    citation_by_chunk[chunk_id]
                    for chunk_id in (str(cid) for cid in item.get("chunkIds", []))
                    if chunk_id in selected_chunk_ids
                ),
                currentness_note=(
                    "Nguồn chưa có kết luận hiện hành rõ ràng; cần đối chiếu các bằng chứng."
                    if any(
                        peer.get("decision") in {"PENDING", "OTHER"}
                        for peer in item.get("conflictPeers") or []
                    )
                    else (
                        "Nguồn không nêu thời điểm hiệu lực; không thể khẳng định thông tin còn hiện hành."
                        if not item.get("validFrom")
                        else None
                    )
                ),
            )
            for item in fact_hits[:top_k]
            if item.get("statement")
            and selected_chunk_ids.intersection(
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
        if not sufficient:
            return EvidenceBundle(
                status=EvidenceStatus.INSUFFICIENT,
                abstention_message=INSUFFICIENT_EVIDENCE,
            )
        return EvidenceBundle(
            status=EvidenceStatus.READY,
            passages=[
                GroundedPassage(
                    citation=str(item["citation"]), content=str(item["content"] or "")
                )
                for item in passages
            ],
            facts=public_facts,
        )

    async def _optional_path(
        self, name: str, awaitable: Any
    ) -> tuple[list[dict[str, Any]], bool]:
        try:
            result = await asyncio.wait_for(
                awaitable, timeout=self._path_timeout_seconds
            )
            return result, True
        except Exception:
            logger.warning("GraphRAG path unavailable: %s", name, exc_info=True)
            return [], False


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
        if normalized
        in {
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


__all__ = [
    "INSUFFICIENT_EVIDENCE",
    "EvidenceBundle",
    "EvidenceStatus",
    "GraphEvidenceRetriever",
    "GraphQaRequest",
    "GraphRAGRetriever",
    "GroundedFact",
    "GroundedPassage",
]
