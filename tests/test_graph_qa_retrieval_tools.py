"""Tests for the deterministic Graph QA retrieval seam."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.session import Session
from google.genai import types

from app.agent.domains.graph_qa.retrieval_agent import (
    EVIDENCE_STATE_KEY,
    QUESTION_STATE_KEY,
    GraphQaRetrievalAdapter,
)
from app.services.graphrag.retrieval import (
    EvidenceBundle,
    EvidenceStatus,
    GraphQaRequest,
    GraphRAGRetriever,
)


def _chunk(chunk_id: str = "c1") -> dict:
    return {
        "chunkId": chunk_id,
        "documentId": "doc1",
        "documentName": "intro.md",
        "versionId": "v1",
        "chunkIndex": 0,
        "text": "Hệ thống có 6 cơ sở tại Hà Đông.",
        "scopeKey": "training",
        "embedding": [0.1, 0.2, 0.3],
        "section": "Cơ sở tập luyện",
    }


def _retriever(*, embedding_side_effect=None, fulltext_result=None):
    embedder = SimpleNamespace(
        embed_query=AsyncMock(
            return_value=[0.1, 0.2, 0.3], side_effect=embedding_side_effect
        )
    )
    store = SimpleNamespace(
        search_chunk_vector=AsyncMock(return_value=[_chunk()]),
        search_chunk_fulltext=AsyncMock(return_value=_chunk_result(fulltext_result)),
        search_entity_fulltext=AsyncMock(return_value=[]),
        search_fact_vector=AsyncMock(return_value=[]),
        chunks_for_entities=AsyncMock(return_value=[]),
        chunks_by_ids=AsyncMock(return_value=[]),
    )
    return GraphRAGRetriever(
        store,
        embedder,
        min_cosine_score=0.55,
        candidate_limit=10,
        context_max_chars=2000,
        retrieval_timeout_seconds=2,
        path_timeout_seconds=1,
    ), store


def _chunk_result(value):
    return [_chunk()] if value is None else value


@pytest.mark.anyio
async def test_hybrid_retrieval_returns_safe_grounded_bundle() -> None:
    retriever, store = _retriever()

    result = await retriever.retrieve(GraphQaRequest(question="Có bao nhiêu cơ sở?"))

    assert result.status is EvidenceStatus.READY
    assert result.passages[0].content == "Hệ thống có 6 cơ sở tại Hà Đông."
    assert "score" not in result.model_dump(mode="json")
    assert store.search_chunk_vector.await_count == 1
    assert store.search_chunk_fulltext.await_count == 1
    assert store.search_entity_fulltext.await_count == 1
    assert store.search_fact_vector.await_count == 1


@pytest.mark.anyio
async def test_hybrid_retrieval_degrades_to_lexical_evidence_when_embedding_fails() -> (
    None
):
    retriever, store = _retriever(embedding_side_effect=RuntimeError("embedding down"))

    result = await retriever.retrieve(GraphQaRequest(question="cơ sở ở đâu"))

    assert result.status is EvidenceStatus.READY
    assert len(result.passages) == 1
    assert store.search_chunk_vector.await_count == 0
    assert store.search_fact_vector.await_count == 0


@pytest.mark.anyio
async def test_hybrid_retrieval_abstains_when_all_paths_have_no_evidence() -> None:
    retriever, store = _retriever(fulltext_result=[])
    store.search_chunk_vector = AsyncMock(return_value=[])

    result = await retriever.retrieve(GraphQaRequest(question="không có dữ liệu"))

    assert result.status is EvidenceStatus.INSUFFICIENT
    assert result.passages == []
    assert result.abstention_message


@pytest.mark.anyio
async def test_retrieval_adapter_calls_the_retriever_once_and_stores_safe_bundle() -> (
    None
):
    class FakeRetriever:
        def __init__(self) -> None:
            self.calls = 0

        async def retrieve(self, _request):
            self.calls += 1
            return EvidenceBundle(
                status=EvidenceStatus.INSUFFICIENT,
                abstention_message="none",
            )

    fake = FakeRetriever()
    adapter = GraphQaRetrievalAdapter(retriever=fake)
    session = Session(id="test", app_name="test", user_id="test")
    ctx = InvocationContext(
        session=session,
        session_service=InMemorySessionService(),
        invocation_id="test-invocation",
        agent=adapter,
        user_content=types.Content(role="user", parts=[types.Part(text="CLB tên gì?")]),
    )

    _ = [event async for event in adapter.run_async(ctx)]

    assert fake.calls == 1
    assert session.state[QUESTION_STATE_KEY] == "CLB tên gì?"
    assert session.state[EVIDENCE_STATE_KEY]["status"] == "INSUFFICIENT"
