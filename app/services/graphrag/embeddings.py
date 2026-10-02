"""Gemini document/query embeddings with a symmetric retrieval contract."""

from __future__ import annotations

import math
from collections.abc import Sequence

from google import genai
from google.genai import types


class EmbeddingError(RuntimeError):
    pass


class GeminiEmbeddingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-embedding-001",
        dimension: int = 768,
        batch_size: int = 64,
    ) -> None:
        if not api_key.strip():
            raise EmbeddingError("GOOGLE_API_KEY/GEMINI_API_KEY is required for GraphRAG")
        if dimension <= 0:
            raise ValueError("Embedding dimension must be positive")
        self.model = model
        self.dimension = dimension
        self.batch_size = max(1, batch_size)
        self._client = genai.Client(api_key=api_key)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed([text], task_type="RETRIEVAL_QUERY")
        return vectors[0]

    async def _embed(
        self, texts: Sequence[str], *, task_type: str
    ) -> list[list[float]]:
        cleaned = [text.strip() for text in texts]
        if any(not text for text in cleaned):
            raise EmbeddingError("Embedding input must not be empty")
        result: list[list[float]] = []
        for start in range(0, len(cleaned), self.batch_size):
            batch = cleaned[start : start + self.batch_size]
            try:
                response = await self._client.aio.models.embed_content(
                    model=self.model,
                    contents=batch,
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=self.dimension,
                    ),
                )
            except Exception as exc:
                raise EmbeddingError(
                    f"{task_type} embedding failed ({type(exc).__name__}): {exc}"
                ) from exc
            embeddings = response.embeddings or []
            if len(embeddings) != len(batch):
                raise EmbeddingError(
                    f"Embedding count mismatch: expected {len(batch)}, got {len(embeddings)}"
                )
            for embedding in embeddings:
                vector = [float(value) for value in (embedding.values or [])]
                if len(vector) != self.dimension:
                    raise EmbeddingError(
                        f"Embedding dimension mismatch: expected {self.dimension}, got {len(vector)}"
                    )
                result.append(_normalize(vector))
        return result


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    return float(sum(a * b for a, b in zip(left, right, strict=True))) / (
        math.sqrt(sum(value * value for value in left))
        * math.sqrt(sum(value * value for value in right))
        or 1.0
    )


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise EmbeddingError("Embedding vector has zero norm")
    return [value / norm for value in vector]


__all__ = ["EmbeddingError", "GeminiEmbeddingProvider", "cosine_similarity"]
