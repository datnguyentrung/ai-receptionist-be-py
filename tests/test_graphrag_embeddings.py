"""Tests for Gemini embedding batching and rate-limit recovery."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.graphrag.embeddings import GeminiEmbeddingProvider


def _response() -> SimpleNamespace:
    return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0, 0.0])])


@pytest.mark.anyio
async def test_successful_embedding_batches_do_not_sleep(monkeypatch) -> None:
    provider = GeminiEmbeddingProvider(
        api_key="test", dimension=2, batch_size=1, min_request_interval_seconds=0
    )
    embed = AsyncMock(side_effect=[_response(), _response()])
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(embed_content=embed)))
    sleep = AsyncMock()
    monkeypatch.setattr("app.services.graphrag.embeddings.asyncio.sleep", sleep)

    vectors = await provider.embed_documents(["first", "second"])

    assert vectors == [[1.0, 0.0], [1.0, 0.0]]
    assert embed.await_count == 2
    sleep.assert_not_awaited()


@pytest.mark.anyio
async def test_rate_limit_waits_before_retry(monkeypatch) -> None:
    provider = GeminiEmbeddingProvider(api_key="test", dimension=2)
    embed = AsyncMock(side_effect=[RuntimeError("429"), _response()])
    provider._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(embed_content=embed)))
    sleep = AsyncMock()
    monkeypatch.setattr("app.services.graphrag.embeddings.asyncio.sleep", sleep)

    vectors = await provider.embed_documents(["first"])

    assert vectors == [[1.0, 0.0]]
    assert embed.await_count == 2
    sleep.assert_awaited_once()
