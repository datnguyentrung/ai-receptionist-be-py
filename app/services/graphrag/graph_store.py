"""Neo4j graph store and query helpers for GraphRAG."""

from typing import Any, cast

from neo4j import AsyncDriver

from app.services.graphrag.embeddings import GeminiEmbeddingProvider

CHUNK_VECTOR_INDEX = "taekwondo_chunk_embedding"
FACT_VECTOR_INDEX = "taekwondo_fact_embedding"
CHUNK_FULLTEXT_INDEX = "taekwondo_chunk_text"
ENTITY_FULLTEXT_INDEX = "taekwondo_entity_search_text"


def _require_tenant(tenant_id: str) -> str:
    cleaned = (tenant_id or "").strip()
    if not cleaned:
        raise ValueError("tenant_id is required for Neo4jGraphStore queries")
    return cleaned


_PUBLIC_ACTIVE_VERSION_FILTER = (
    "coalesce(v.visibility, 'PUBLIC') = 'PUBLIC' "
    "AND (v.effectiveFrom IS NULL OR date(v.effectiveFrom) <= date()) "
    "AND (v.effectiveTo IS NULL OR date(v.effectiveTo) >= date())"
)


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
                await session.run(cast(Any, statement))

    async def search_chunk_vector(
        self, query_vector: list[float], limit: int, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        resolved_tenant = _require_tenant(tenant_id)
        query = f"""
        CALL db.index.vector.queryNodes('{CHUNK_VECTOR_INDEX}', $limit, $query_vector)
        YIELD node, score
        MATCH (v:TaekwondoSourceVersion {{versionId: node.versionId, status: 'COMMITTED', tenantId: $tenant_id}})
        WHERE node.tenantId = $tenant_id AND {_PUBLIC_ACTIVE_VERSION_FILTER}
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
            score
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query,
                limit=limit,
                query_vector=query_vector,
                tenant_id=resolved_tenant,
            )
            return [dict(record) async for record in result]

    async def search_chunk_fulltext(
        self, lucene_query: str, limit: int, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        resolved_tenant = _require_tenant(tenant_id)
        query = f"""
        CALL db.index.fulltext.queryNodes('{CHUNK_FULLTEXT_INDEX}', $lucene_query, {{limit: $limit}})
        YIELD node, score
        MATCH (v:TaekwondoSourceVersion {{versionId: node.versionId, status: 'COMMITTED', tenantId: $tenant_id}})
        WHERE node.tenantId = $tenant_id AND {_PUBLIC_ACTIVE_VERSION_FILTER}
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
            score
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query,
                lucene_query=lucene_query,
                limit=limit,
                tenant_id=resolved_tenant,
            )
            return [dict(record) async for record in result]

    async def search_entity_fulltext(
        self, lucene_query: str, limit: int, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        resolved_tenant = _require_tenant(tenant_id)
        query = f"""
        CALL db.index.fulltext.queryNodes('{ENTITY_FULLTEXT_INDEX}', $lucene_query, {{limit: $limit}})
        YIELD node, score
        MATCH (v:TaekwondoSourceVersion {{status: 'COMMITTED', tenantId: $tenant_id}})-[:ASSERTS_ENTITY]->(node)
        WHERE node.tenantId = $tenant_id AND {_PUBLIC_ACTIVE_VERSION_FILTER}
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
                tenant_id=resolved_tenant,
            )
            return [dict(record) async for record in result]

    async def search_fact_vector(
        self, query_vector: list[float], limit: int, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        resolved_tenant = _require_tenant(tenant_id)
        query = f"""
        CALL db.index.vector.queryNodes('{FACT_VECTOR_INDEX}', $limit, $query_vector)
        YIELD node, score
        MATCH (v:TaekwondoSourceVersion {{status: 'COMMITTED', tenantId: $tenant_id}})-[:ASSERTS_FACT]->(node)
        WHERE node.tenantId = $tenant_id AND {_PUBLIC_ACTIVE_VERSION_FILTER}
          AND NOT EXISTS {{ MATCH (node)-[r:CONFLICTS_WITH]->() WHERE r.decision = 'REJECT' }}
          AND NOT EXISTS {{ MATCH ()-[r:CONFLICTS_WITH]->(node) WHERE r.decision = 'CONFIRM' }}
        OPTIONAL MATCH (node)-[review:CONFLICTS_WITH]-(other:TaekwondoKnowledgeFact {{tenantId: $tenant_id}})
        OPTIONAL MATCH (node)-[:SUPPORTED_BY]->(chunk:TaekwondoChunk {{tenantId: $tenant_id}})
        WITH node, score, collect(DISTINCT {{factId: other.factId,
          decision: review.decision}}) AS conflictPeers,
          collect(DISTINCT chunk.chunkId) AS chunkIds
        RETURN
            node.factId AS factId,
            node.statement AS statement,
            node.factType AS factType,
            NULL AS validFrom,
            NULL AS validTo,
            chunkIds AS chunkIds,
            conflictPeers AS conflictPeers,
            score
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query,
                limit=limit,
                query_vector=query_vector,
                tenant_id=resolved_tenant,
            )
            return [dict(record) async for record in result]

    async def chunks_for_entities(
        self, entity_keys: list[str], limit: int, *, tenant_id: str
    ) -> list[dict[str, Any]]:
        resolved_tenant = _require_tenant(tenant_id)
        if not entity_keys:
            return []
        query = f"""
        MATCH (e:TaekwondoKnowledgeEntity {{tenantId: $tenant_id}})
        WHERE e.stableKey IN $entity_keys
        MATCH (e)-[:MENTIONED_IN]->(c:TaekwondoChunk {{tenantId: $tenant_id}})
        MATCH (v:TaekwondoSourceVersion {{versionId: c.versionId, status: 'COMMITTED', tenantId: $tenant_id}})
        WHERE {_PUBLIC_ACTIVE_VERSION_FILTER}
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
            c.sourceAnchor AS sourceAnchor
        LIMIT $limit
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query,
                entity_keys=entity_keys,
                limit=limit,
                tenant_id=resolved_tenant,
            )
            return [dict(record) async for record in result]

    async def chunks_by_ids(
        self, chunk_ids: list[str], *, tenant_id: str, include_historical: bool = False
    ) -> list[dict[str, Any]]:
        resolved_tenant = _require_tenant(tenant_id)
        if not chunk_ids:
            return []
        query = """
        MATCH (c:TaekwondoChunk {tenantId: $tenant_id})
        WHERE c.chunkId IN $chunk_ids
        MATCH (v:TaekwondoSourceVersion {versionId: c.versionId, tenantId: $tenant_id})
        WHERE v.status = 'COMMITTED' OR ($include_historical AND v.status = 'SUPERSEDED')
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
            c.sourceAnchor AS sourceAnchor
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query,
                chunk_ids=chunk_ids,
                tenant_id=resolved_tenant,
                include_historical=include_historical,
            )
            return [dict(record) async for record in result]

    async def document_history(self, document_key: str, *, tenant_id: str) -> list[dict[str, Any]]:
        """Read current and superseded assertions with their original citations for one tenant."""
        resolved_tenant = _require_tenant(tenant_id)
        query = """
        MATCH (v:TaekwondoSourceVersion {documentKey: $document_key, tenantId: $tenant_id})
        WHERE v.status IN ['COMMITTED', 'SUPERSEDED']
        OPTIONAL MATCH (v)-[assertion:ASSERTS_FACT]->(f:TaekwondoKnowledgeFact {tenantId: $tenant_id})
        OPTIONAL MATCH (f)-[:SUPPORTED_BY]->(c:TaekwondoChunk {tenantId: $tenant_id})
        WITH v, f, collect(DISTINCT c.sourceAnchor) AS anchors
        RETURN v.versionId AS versionId, v.status AS status,
               v.documentKey AS documentKey, v.documentName AS documentName,
               v.committedAt AS committedAt, f.factId AS factId,
               f.statement AS statement, f.predicate AS predicate,
               f.valueJson AS valueJson, assertion.validFrom AS validFrom,
               assertion.validTo AS validTo, anchors AS sourceAnchors
        ORDER BY v.committedAt DESC, factId
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query, document_key=document_key, tenant_id=resolved_tenant
            )
            return [dict(record) async for record in result]


__all__ = ["Neo4jGraphStore"]
