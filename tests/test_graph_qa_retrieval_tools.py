"""Unit tests for execute_read_cypher and vector_search tools."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.tools import graphrag_tools


def test_execute_read_cypher_blocks_write_keywords() -> None:
    result = asyncio.run(graphrag_tools.execute_read_cypher("CREATE (n:Foo) RETURN n"))
    assert result["success"] is False
    assert result["errorCode"] == "INVALID_CYPHER"


def test_execute_read_cypher_success(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_session = AsyncMock()

    class FakeRecord:
        def items(self):
            return [("name", "Dollyo Chagi"), ("points", 2)]

    async def fake_iter():
        yield FakeRecord()

    # In neo4j driver, `await session.run(...)` returns an async iterator
    mock_result = MagicMock()
    mock_result.__aiter__ = lambda self: fake_iter()
    mock_session.run = AsyncMock(return_value=mock_result)

    mock_driver = MagicMock()
    mock_driver.session.return_value.__aenter__.return_value = mock_session

    fake_container = SimpleNamespace(neo4j_driver=mock_driver)
    monkeypatch.setattr(
        graphrag_tools,
        "get_service_container",
        AsyncMock(return_value=fake_container),
    )

    context = SimpleNamespace(state={})
    result = asyncio.run(
        graphrag_tools.execute_read_cypher(
            "MATCH (n:Technique) RETURN n.name, n.points LIMIT 1",
            tool_context=context,
        )
    )

    assert result["success"] is True
    assert result["rowCount"] == 1
    assert result["rows"] == [{"name": "Dollyo Chagi", "points": 2}]
    assert "last_cypher_query" in context.state
    assert context.state["last_cypher_query"]["rowCount"] == 1


def test_vector_search_rejects_empty_query() -> None:
    result = asyncio.run(graphrag_tools.vector_search(""))
    assert result["success"] is False
    assert result["errorCode"] == "EMPTY_QUERY"


def test_vector_search_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_embedding = [0.1, 0.2, 0.3]
    mock_embedder = AsyncMock()
    mock_embedder.embed_query = AsyncMock(return_value=fake_embedding)

    mock_store = AsyncMock()
    mock_store.search_chunk_vector = AsyncMock(
        return_value=[
            {
                "chunkId": "c1",
                "documentId": "doc1",
                "versionId": "v1",
                "text": "Dollyo Chagi kicking technique",
                "scopeKey": "technique",
                "embedding": [0.1, 0.2, 0.3],
            }
        ]
    )
    mock_store.search_fact_vector = AsyncMock(
        return_value=[
            {
                "factId": "f1",
                "statement": "Dollyo Chagi earns 2 points",
            }
        ]
    )

    fake_container = SimpleNamespace(
        embedding_provider=mock_embedder,
        rag_graph_store=mock_store,
    )
    monkeypatch.setattr(
        graphrag_tools,
        "get_service_container",
        AsyncMock(return_value=fake_container),
    )

    context = SimpleNamespace(state={})
    result = asyncio.run(
        graphrag_tools.vector_search(
            "kicking technique",
            scope_key="technique",
            top_k=5,
            tool_context=context,
        )
    )

    assert result["success"] is True
    assert result["strategy"] == "vector_search"
    assert result["sufficientEvidence"] is True
    assert len(result["passages"]) == 1
    assert result["passages"][0]["content"] == "Dollyo Chagi kicking technique"
    assert len(result["facts"]) == 1
    assert "last_vector_trace" in context.state
