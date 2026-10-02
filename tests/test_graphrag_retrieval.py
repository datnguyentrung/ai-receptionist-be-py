import asyncio

from app.services.graphrag.retrieval import INSUFFICIENT_EVIDENCE, GraphRAGRetriever


def _chunk(chunk_id: str, text: str, embedding: list[float], score: float = 0.8):
    return {
        "chunkId": chunk_id,
        "versionId": "v1",
        "documentId": "d1",
        "documentName": "handbook.md",
        "chunkIndex": 0,
        "text": text,
        "section": "Lịch học",
        "pageStart": None,
        "pageEnd": None,
        "sourceAnchor": f"handbook.md#{chunk_id}",
        "scopeKey": "training",
        "embedding": embedding,
        "score": score,
    }


class FakeEmbeddingProvider:
    async def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]


class FakeGraphStore:
    def __init__(self, relevant: bool = True) -> None:
        vector = [0.99, 0.01] if relevant else [0.0, 1.0]
        self.item = _chunk("c1", "Lớp cơ bản học vào thứ Hai.", vector)

    async def search_chunk_vector(self, embedding, limit):
        return [dict(self.item)]

    async def search_chunk_fulltext(self, query, limit):
        return [dict(self.item)]

    async def search_entity_fulltext(self, query, limit):
        return [{"stableKey": "e1", "score": 1.0}]

    async def search_fact_vector(self, embedding, limit):
        return [
            {
                "factId": "f1",
                "statement": "Lớp cơ bản học vào thứ Hai",
                "factType": "PROPERTY",
                "score": 0.9,
                "chunkIds": ["c1"],
            }
        ]

    async def chunks_for_entities(self, stable_keys, limit):
        return [dict(self.item)]

    async def chunks_by_ids(self, chunk_ids):
        return [dict(self.item)]


def test_hybrid_retrieval_deduplicates_and_returns_citation() -> None:
    retriever = GraphRAGRetriever(
        FakeGraphStore(),
        FakeEmbeddingProvider(),
        min_cosine_score=0.55,
        candidate_limit=20,
        context_max_chars=5000,
    )
    result = asyncio.run(retriever.retrieve("Lớp cơ bản học ngày nào?", top_k=10))

    assert result["sufficientEvidence"] is True
    assert len(result["passages"]) == 1
    assert result["passages"][0]["citation"] == "handbook.md — Lịch học"
    assert set(result["passages"][0]["retrievalPaths"]) == {
        "chunkFulltext",
        "chunkVector",
        "entityTraversal",
        "factSupport",
    }


def test_irrelevant_retrieval_abstains_and_hides_context() -> None:
    retriever = GraphRAGRetriever(
        FakeGraphStore(relevant=False),
        FakeEmbeddingProvider(),
        min_cosine_score=0.55,
        candidate_limit=20,
        context_max_chars=5000,
    )
    result = asyncio.run(retriever.retrieve("Giá Bitcoin hôm nay?"))

    assert result["sufficientEvidence"] is False
    assert result["abstentionMessage"] == INSUFFICIENT_EVIDENCE
    assert result["passages"] == []
    assert result["facts"] == []


def test_scope_filter_can_force_abstention() -> None:
    retriever = GraphRAGRetriever(
        FakeGraphStore(),
        FakeEmbeddingProvider(),
        min_cosine_score=0.55,
        candidate_limit=20,
        context_max_chars=5000,
    )
    result = asyncio.run(
        retriever.retrieve("Lịch học?", scope_key="finance", top_k=10)
    )
    assert result["sufficientEvidence"] is False
