"""Neo4j graph store and query helpers for GraphRAG."""

from collections.abc import Iterable
from itertools import pairwise
from typing import Any

from neo4j import AsyncDriver

from app.services.graphrag.embeddings import GeminiEmbeddingProvider

CHUNK_VECTOR_INDEX = "taekwondo_chunk_embedding"
FACT_VECTOR_INDEX = "taekwondo_fact_embedding"
CHUNK_FULLTEXT_INDEX = "taekwondo_chunk_text"
ENTITY_FULLTEXT_INDEX = "taekwondo_entity_search_text"


class Neo4jGraphStore:
    """Provides vector and fulltext retrieval methods against Neo4j knowledge graph."""

    def __init__(
        self,
        driver: AsyncDriver,
        database: str,
        *,
        embedding_provider: GeminiEmbeddingProvider,
    ) -> None:
        self._driver = driver
        self._database = database
        self._embedding_provider = embedding_provider

    async def ensure_indexes(self, embedding_dimension: int) -> None:
        """Create the semantic indexes required by retrieval, idempotently."""
        statements = (
            f"""
            CREATE VECTOR INDEX {CHUNK_VECTOR_INDEX} IF NOT EXISTS
            FOR (n:TaekwondoChunk) ON (n.embedding)
            OPTIONS {{indexConfig: {{
              `vector.dimensions`: {int(embedding_dimension)},
              `vector.similarity_function`: 'cosine'
            }}}}
            """,
            f"""
            CREATE VECTOR INDEX {FACT_VECTOR_INDEX} IF NOT EXISTS
            FOR (n:TaekwondoKnowledgeFact) ON (n.embedding)
            OPTIONS {{indexConfig: {{
              `vector.dimensions`: {int(embedding_dimension)},
              `vector.similarity_function`: 'cosine'
            }}}}
            """,
            f"""
            CREATE FULLTEXT INDEX {CHUNK_FULLTEXT_INDEX} IF NOT EXISTS
            FOR (n:TaekwondoChunk) ON EACH [n.text]
            """,
            f"""
            CREATE FULLTEXT INDEX {ENTITY_FULLTEXT_INDEX} IF NOT EXISTS
            FOR (n:TaekwondoKnowledgeEntity) ON EACH [n.searchText]
            """,
        )
        async with self._driver.session(database=self._database) as session:
            for statement in statements:
                await session.run(statement)

    async def search_chunk_vector(
        self, query_vector: list[float], limit: int
    ) -> list[dict[str, Any]]:
        query = f"""
        CALL db.index.vector.queryNodes('{CHUNK_VECTOR_INDEX}', $limit, $query_vector)
        YIELD node, score
        RETURN
            node.chunkId AS chunkId,
            node.documentId AS documentId,
            node.documentName AS documentName,
            node.versionId AS versionId,
            node.chunkIndex AS chunkIndex,
            node.text AS text,
            node.scopeKey AS scopeKey,
            node.scopeKeys AS scopeKeys,
            node.section AS section,
            node.pageStart AS pageStart,
            node.pageEnd AS pageEnd,
            node.sourceAnchor AS sourceAnchor,
            node.embedding AS embedding,
            score
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query,
                limit=limit,
                query_vector=query_vector,
            )
            return [dict(record) async for record in result]

    async def search_chunk_fulltext(
        self, lucene_query: str, limit: int
    ) -> list[dict[str, Any]]:
        query = f"""
        CALL db.index.fulltext.queryNodes('{CHUNK_FULLTEXT_INDEX}', $lucene_query, {{limit: $limit}})
        YIELD node, score
        RETURN
            node.chunkId AS chunkId,
            node.documentId AS documentId,
            node.documentName AS documentName,
            node.versionId AS versionId,
            node.chunkIndex AS chunkIndex,
            node.text AS text,
            node.scopeKey AS scopeKey,
            node.scopeKeys AS scopeKeys,
            node.section AS section,
            node.pageStart AS pageStart,
            node.pageEnd AS pageEnd,
            node.sourceAnchor AS sourceAnchor,
            node.embedding AS embedding,
            score
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query,
                lucene_query=lucene_query,
                limit=limit,
            )
            return [dict(record) async for record in result]

    async def search_entity_fulltext(
        self, lucene_query: str, limit: int
    ) -> list[dict[str, Any]]:
        query = f"""
        CALL db.index.fulltext.queryNodes('{ENTITY_FULLTEXT_INDEX}', $lucene_query, {{limit: $limit}})
        YIELD node, score
        RETURN
            node.stableKey AS stableKey,
            node.name AS name,
            node.entityType AS entityType,
            node.scopeKey AS scopeKey,
            node.scopeKeys AS scopeKeys,
            score
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query,
                lucene_query=lucene_query,
                limit=limit,
            )
            return [dict(record) async for record in result]

    async def search_fact_vector(
        self, query_vector: list[float], limit: int
    ) -> list[dict[str, Any]]:
        query = f"""
        CALL db.index.vector.queryNodes('{FACT_VECTOR_INDEX}', $limit, $query_vector)
        YIELD node, score
        RETURN
            node.factId AS factId,
            node.statement AS statement,
            node.factType AS factType,
            node.chunkIds AS chunkIds,
            score
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query,
                limit=limit,
                query_vector=query_vector,
            )
            return [dict(record) async for record in result]

    async def chunks_for_entities(
        self, entity_keys: list[str], limit: int
    ) -> list[dict[str, Any]]:
        if not entity_keys:
            return []
        query = """
        MATCH (e:TaekwondoKnowledgeEntity)
        WHERE e.stableKey IN $entity_keys
        MATCH (c:TaekwondoChunk)-[:MENTIONS|HAS_FACT]->(e)
        RETURN DISTINCT
            c.chunkId AS chunkId,
            c.documentId AS documentId,
            c.documentName AS documentName,
            c.versionId AS versionId,
            c.chunkIndex AS chunkIndex,
            c.text AS text,
            c.scopeKey AS scopeKey,
            c.scopeKeys AS scopeKeys,
            c.section AS section,
            c.pageStart AS pageStart,
            c.pageEnd AS pageEnd,
            c.sourceAnchor AS sourceAnchor,
            c.embedding AS embedding
        LIMIT $limit
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query,
                entity_keys=entity_keys,
                limit=limit,
            )
            return [dict(record) async for record in result]

    async def chunks_by_ids(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        if not chunk_ids:
            return []
        query = """
        MATCH (c:TaekwondoChunk)
        WHERE c.chunkId IN $chunk_ids
        RETURN
            c.chunkId AS chunkId,
            c.documentId AS documentId,
            c.documentName AS documentName,
            c.versionId AS versionId,
            c.chunkIndex AS chunkIndex,
            c.text AS text,
            c.scopeKey AS scopeKey,
            c.scopeKeys AS scopeKeys,
            c.section AS section,
            c.pageStart AS pageStart,
            c.pageEnd AS pageEnd,
            c.sourceAnchor AS sourceAnchor,
            c.embedding AS embedding
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(query, chunk_ids=chunk_ids)
            return [dict(record) async for record in result]


__all__ = ["Neo4jGraphStore"]
