"""Neo4j persistence and retrieval primitives for Taekwondo GraphRAG."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from itertools import pairwise
from typing import Any

from neo4j import AsyncDriver

from app.services.graphrag.embeddings import GeminiEmbeddingProvider
from app.services.ingestion.repository import Workspace

CHUNK_VECTOR_INDEX = "taekwondo_chunk_embedding"
FACT_VECTOR_INDEX = "taekwondo_fact_embedding"
CHUNK_FULLTEXT_INDEX = "taekwondo_chunk_text"
ENTITY_FULLTEXT_INDEX = "taekwondo_entity_search_text"


class Neo4jIngestionStore:
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
                await (await session.run(statement)).consume()

    async def stage_batch(
        self, ingestion_id: str, batch_index: int, fragment: dict
    ) -> None:
        payload = json.dumps(fragment, ensure_ascii=False, sort_keys=True)
        async with self._driver.session(database=self._database) as session:

            async def write_staging(tx: Any) -> None:
                result = await tx.run(
                    """
                    MERGE (b:TaekwondoIngestionBatch {
                      ingestionId: $ingestion_id, batchIndex: $batch_index
                    })
                    SET b.fragmentJson = $payload, b.status = 'STAGED',
                        b.updatedAt = datetime()
                    """,
                    ingestion_id=ingestion_id,
                    batch_index=batch_index,
                    payload=payload,
                )
                await result.consume()

            await session.execute_write(write_staging)

    async def fill(self, workspace: Workspace) -> dict[str, Any]:
        """Write domain graph, source chunks, facts, embeddings, and provenance atomically."""

        fragments = [item.graph_fragment for item in workspace.batches if item.graph_fragment]
        nodes, edges = _merge_fragments(fragments)
        payload = await _build_graphrag_payload(
            workspace,
            nodes,
            edges,
            self._embedding_provider,
        )
        async with self._driver.session(database=self._database) as session:
            return await session.execute_write(self._write_and_verify, payload)

    @staticmethod
    async def _write_and_verify(tx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        await (
            await tx.run(
                """
                MERGE (v:TaekwondoSourceVersion {versionId: $version_id})
                SET v.documentId = $document_id,
                    v.documentName = $document_name,
                    v.ontologyVersionId = $ontology_version_id,
                    v.status = 'WRITTEN', v.updatedAt = datetime()
                """,
                **_pick(
                    payload,
                    "version_id",
                    "document_id",
                    "document_name",
                    "ontology_version_id",
                ),
            )
        ).consume()

        await (
            await tx.run(
                """
                UNWIND $chunks AS chunk
                MATCH (v:TaekwondoSourceVersion {versionId: $version_id})
                MERGE (c:TaekwondoChunk {
                  versionId: $version_id, chunkId: chunk.chunkId
                })
                SET c.documentId = $document_id,
                    c.documentName = $document_name,
                    c.chunkIndex = chunk.chunkIndex,
                    c.text = chunk.text,
                    c.contentHash = chunk.contentHash,
                    c.tokenCount = chunk.tokenCount,
                    c.section = chunk.section,
                    c.pageStart = chunk.pageStart,
                    c.pageEnd = chunk.pageEnd,
                    c.sourceAnchor = chunk.sourceAnchor,
                    c.scopeKey = chunk.scopeKey,
                    c.embedding = chunk.embedding,
                    c.embeddingModel = $embedding_model,
                    c.embeddingVersion = $embedding_model,
                    c.embeddingDimension = size(chunk.embedding),
                    c.active = true,
                    c.updatedAt = datetime()
                MERGE (v)-[:HAS_CHUNK]->(c)
                """,
                chunks=payload["chunks"],
                **_pick(
                    payload,
                    "version_id",
                    "document_id",
                    "document_name",
                    "embedding_model",
                ),
            )
        ).consume()

        if payload["chunk_links"]:
            await (
                await tx.run(
                    """
                    UNWIND $links AS link
                    MATCH (a:TaekwondoChunk {versionId: $version_id, chunkId: link.from})
                    MATCH (b:TaekwondoChunk {versionId: $version_id, chunkId: link.to})
                    MERGE (a)-[:NEXT_CHUNK {versionId: $version_id}]->(b)
                    """,
                    links=payload["chunk_links"],
                    version_id=payload["version_id"],
                )
            ).consume()

        await (
            await tx.run(
                """
                UNWIND $nodes AS node
                MATCH (v:TaekwondoSourceVersion {versionId: $version_id})
                MERGE (n:TaekwondoKnowledgeEntity {stableKey: node.stableKey})
                ON CREATE SET n.createdAt = datetime()
                SET n.entityType = node.className,
                    n.identityJson = node.identityJson,
                    n.propertiesJson = node.propertiesJson,
                    n.searchText = node.searchText,
                    n.updatedAt = datetime()
                MERGE (v)-[:ASSERTS_ENTITY {tempId: node.tempId}]->(n)
                """,
                nodes=payload["nodes"],
                version_id=payload["version_id"],
            )
        ).consume()

        if payload["mentions"]:
            await (
                await tx.run(
                    """
                    UNWIND $mentions AS mention
                    MATCH (n:TaekwondoKnowledgeEntity {stableKey: mention.stableKey})
                    MATCH (c:TaekwondoChunk {
                      versionId: $version_id, chunkId: mention.chunkId
                    })
                    MERGE (n)-[m:MENTIONED_IN {
                      versionId: $version_id, chunkId: mention.chunkId
                    }]->(c)
                    SET m.quotes = mention.quotes, m.updatedAt = datetime()
                    """,
                    mentions=payload["mentions"],
                    version_id=payload["version_id"],
                )
            ).consume()

        if payload["edges"]:
            await (
                await tx.run(
                    """
                    UNWIND $edges AS edge
                    MATCH (s:TaekwondoKnowledgeEntity {stableKey: edge.sourceKey})
                    MATCH (t:TaekwondoKnowledgeEntity {stableKey: edge.targetKey})
                    MERGE (s)-[r:TAEKWONDO_RELATION {
                      relationType: edge.edgeName,
                      versionId: $version_id,
                      sourceKey: edge.sourceKey,
                      targetKey: edge.targetKey
                    }]->(t)
                    SET r.propertiesJson = edge.propertiesJson,
                        r.evidenceJson = edge.evidenceJson,
                        r.factText = edge.factText,
                        r.updatedAt = datetime()
                    """,
                    edges=payload["edges"],
                    version_id=payload["version_id"],
                )
            ).consume()

        if payload["facts"]:
            await (
                await tx.run(
                    """
                    UNWIND $facts AS fact
                    MATCH (v:TaekwondoSourceVersion {versionId: $version_id})
                    MERGE (f:TaekwondoKnowledgeFact {factId: fact.factId})
                    SET f.versionId = $version_id,
                        f.factType = fact.factType,
                        f.statement = fact.statement,
                        f.predicate = fact.predicate,
                        f.valueJson = fact.valueJson,
                        f.confidence = fact.confidence,
                        f.embedding = fact.embedding,
                        f.embeddingModel = $embedding_model,
                        f.embeddingVersion = $embedding_model,
                        f.ontologyVersionId = $ontology_version_id,
                        f.embeddingDimension = size(fact.embedding),
                        f.active = true,
                        f.updatedAt = datetime()
                    MERGE (v)-[:ASSERTS_FACT]->(f)
                    WITH f, fact
                    MATCH (subject:TaekwondoKnowledgeEntity {stableKey: fact.subjectKey})
                    MERGE (f)-[:SUBJECT]->(subject)
                    """,
                    facts=payload["facts"],
                    version_id=payload["version_id"],
                    embedding_model=payload["embedding_model"],
                    ontology_version_id=payload["ontology_version_id"],
                )
            ).consume()
            # Neo4j FOREACH cannot MATCH the object entity, so wire object links separately.
            await (
                await tx.run(
                    """
                    UNWIND [fact IN $facts WHERE fact.objectKey IS NOT NULL] AS fact
                    MATCH (f:TaekwondoKnowledgeFact {factId: fact.factId})
                    MATCH (object:TaekwondoKnowledgeEntity {stableKey: fact.objectKey})
                    MERGE (f)-[:OBJECT]->(object)
                    WITH f, fact
                    UNWIND fact.evidenceChunkIds AS chunk_id
                    MATCH (c:TaekwondoChunk {versionId: $version_id, chunkId: chunk_id})
                    MERGE (f)-[:SUPPORTED_BY {versionId: $version_id}]->(c)
                    """,
                    facts=payload["facts"],
                    version_id=payload["version_id"],
                )
            ).consume()
            await (
                await tx.run(
                    """
                    UNWIND [fact IN $facts WHERE fact.objectKey IS NULL] AS fact
                    MATCH (f:TaekwondoKnowledgeFact {factId: fact.factId})
                    UNWIND fact.evidenceChunkIds AS chunk_id
                    MATCH (c:TaekwondoChunk {versionId: $version_id, chunkId: chunk_id})
                    MERGE (f)-[:SUPPORTED_BY {versionId: $version_id}]->(c)
                    """,
                    facts=payload["facts"],
                    version_id=payload["version_id"],
                )
            ).consume()

        record = await (
            await tx.run(
                """
                MATCH (v:TaekwondoSourceVersion {versionId: $version_id})
                OPTIONAL MATCH (v)-[:ASSERTS_ENTITY]->(n:TaekwondoKnowledgeEntity)
                WITH v, count(DISTINCT n) AS nodes
                OPTIONAL MATCH ()-[r:TAEKWONDO_RELATION {versionId: $version_id}]->()
                WITH v, nodes, count(DISTINCT r) AS edges
                OPTIONAL MATCH (v)-[:HAS_CHUNK]->(c:TaekwondoChunk)
                WITH v, nodes, edges, count(DISTINCT c) AS chunks
                OPTIONAL MATCH (v)-[:ASSERTS_FACT]->(f:TaekwondoKnowledgeFact)
                SET v.status = 'COMMITTED', v.committedAt = datetime()
                RETURN nodes, edges, chunks, count(DISTINCT f) AS facts
                """,
                version_id=payload["version_id"],
            )
        ).single()
        actual = {
            "nodes": int(record["nodes"] if record else 0),
            "edges": int(record["edges"] if record else 0),
            "chunks": int(record["chunks"] if record else 0),
            "facts": int(record["facts"] if record else 0),
        }
        expected = {
            "nodes": len(payload["nodes"]),
            "edges": len(payload["edges"]),
            "chunks": len(payload["chunks"]),
            "facts": len(payload["facts"]),
        }
        if actual != expected:
            raise RuntimeError(f"Neo4j read-back mismatch: expected {expected}, got {actual}")
        return {"commitStatus": "COMMITTED", **actual, "readbackVerified": True}

    async def search_chunk_vector(
        self, embedding: list[float], limit: int
    ) -> list[dict[str, Any]]:
        return await self._data(
            """
            CALL db.index.vector.queryNodes($index, $limit, $embedding)
            YIELD node, score
            MATCH (v:TaekwondoSourceVersion {versionId: node.versionId, status: 'COMMITTED'})
            RETURN node.chunkId AS chunkId, node.versionId AS versionId,
                   node.documentId AS documentId, node.documentName AS documentName,
                   node.chunkIndex AS chunkIndex, node.text AS text,
                   node.section AS section, node.pageStart AS pageStart,
                   node.pageEnd AS pageEnd, node.sourceAnchor AS sourceAnchor,
                   node.scopeKey AS scopeKey, node.embedding AS embedding, score
            ORDER BY score DESC
            """,
            index=CHUNK_VECTOR_INDEX,
            limit=limit,
            embedding=embedding,
        )

    async def search_fact_vector(
        self, embedding: list[float], limit: int
    ) -> list[dict[str, Any]]:
        return await self._data(
            """
            CALL db.index.vector.queryNodes($index, $limit, $embedding)
            YIELD node, score
            MATCH (v:TaekwondoSourceVersion {versionId: node.versionId, status: 'COMMITTED'})
            OPTIONAL MATCH (node)-[:SUPPORTED_BY]->(chunk:TaekwondoChunk)
            RETURN node.factId AS factId, node.statement AS statement,
                   node.factType AS factType, score,
                   collect(DISTINCT chunk.chunkId) AS chunkIds
            ORDER BY score DESC
            """,
            index=FACT_VECTOR_INDEX,
            limit=limit,
            embedding=embedding,
        )

    async def search_chunk_fulltext(
        self, query: str, limit: int
    ) -> list[dict[str, Any]]:
        return await self._data(
            """
            CALL db.index.fulltext.queryNodes($index, $query, {limit: $limit})
            YIELD node, score
            MATCH (v:TaekwondoSourceVersion {versionId: node.versionId, status: 'COMMITTED'})
            RETURN node.chunkId AS chunkId, node.versionId AS versionId,
                   node.documentId AS documentId, node.documentName AS documentName,
                   node.chunkIndex AS chunkIndex, node.text AS text,
                   node.section AS section, node.pageStart AS pageStart,
                   node.pageEnd AS pageEnd, node.sourceAnchor AS sourceAnchor,
                   node.scopeKey AS scopeKey, node.embedding AS embedding, score
            ORDER BY score DESC
            """,
            index=CHUNK_FULLTEXT_INDEX,
            query=query,
            limit=limit,
        )

    async def search_entity_fulltext(
        self, query: str, limit: int
    ) -> list[dict[str, Any]]:
        return await self._data(
            """
            CALL db.index.fulltext.queryNodes($index, $query, {limit: $limit})
            YIELD node, score
            WHERE EXISTS {
              MATCH (:TaekwondoSourceVersion {status: 'COMMITTED'})-[:ASSERTS_ENTITY]->(node)
            }
            RETURN node.stableKey AS stableKey, node.entityType AS entityType,
                   node.searchText AS searchText, score
            ORDER BY score DESC
            """,
            index=ENTITY_FULLTEXT_INDEX,
            query=query,
            limit=limit,
        )

    async def chunks_for_entities(
        self, stable_keys: list[str], limit: int
    ) -> list[dict[str, Any]]:
        if not stable_keys:
            return []
        return await self._data(
            """
            MATCH (seed:TaekwondoKnowledgeEntity)
            WHERE seed.stableKey IN $stable_keys
            MATCH path=(seed)-[:TAEKWONDO_RELATION*0..2]-(entity:TaekwondoKnowledgeEntity)
            MATCH (entity)-[:MENTIONED_IN]->(chunk:TaekwondoChunk)
            MATCH (version:TaekwondoSourceVersion {
              versionId: chunk.versionId, status: 'COMMITTED'
            })
            RETURN DISTINCT chunk.chunkId AS chunkId,
                   chunk.versionId AS versionId,
                   chunk.documentId AS documentId,
                   chunk.documentName AS documentName,
                   chunk.chunkIndex AS chunkIndex, chunk.text AS text,
                   chunk.section AS section, chunk.pageStart AS pageStart,
                   chunk.pageEnd AS pageEnd, chunk.sourceAnchor AS sourceAnchor,
                   chunk.scopeKey AS scopeKey, chunk.embedding AS embedding,
                   1.0 / (1.0 + length(path)) AS score
            ORDER BY score DESC
            LIMIT $limit
            """,
            stable_keys=stable_keys,
            limit=limit,
        )

    async def chunks_by_ids(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        if not chunk_ids:
            return []
        return await self._data(
            """
            MATCH (chunk:TaekwondoChunk)
            WHERE chunk.chunkId IN $chunk_ids
            MATCH (:TaekwondoSourceVersion {
              versionId: chunk.versionId, status: 'COMMITTED'
            })
            RETURN DISTINCT chunk.chunkId AS chunkId,
                   chunk.versionId AS versionId,
                   chunk.documentId AS documentId,
                   chunk.documentName AS documentName,
                   chunk.chunkIndex AS chunkIndex, chunk.text AS text,
                   chunk.section AS section, chunk.pageStart AS pageStart,
                   chunk.pageEnd AS pageEnd, chunk.sourceAnchor AS sourceAnchor,
                   chunk.scopeKey AS scopeKey, chunk.embedding AS embedding, 1.0 AS score
            """,
            chunk_ids=chunk_ids,
        )

    async def _data(self, query: str, **parameters: Any) -> list[dict[str, Any]]:
        async with self._driver.session(database=self._database) as session:
            return await (await session.run(query, **parameters)).data()

    async def deactivate_version(self, version_id: str, status: str) -> dict[str, Any]:
        async with self._driver.session(database=self._database) as session:
            return await session.execute_write(self._deactivate_tx, version_id, status)

    async def committed_summary(self, version_id: str) -> dict[str, Any] | None:
        rows = await self._data(
            """
            MATCH (v:TaekwondoSourceVersion {versionId: $version_id, status: 'COMMITTED'})
            OPTIONAL MATCH (v)-[:ASSERTS_ENTITY]->(n:TaekwondoKnowledgeEntity)
            WITH v, count(DISTINCT n) AS nodes
            OPTIONAL MATCH ()-[r:TAEKWONDO_RELATION {versionId: $version_id}]->()
            WITH v, nodes, count(DISTINCT r) AS edges
            OPTIONAL MATCH (v)-[:HAS_CHUNK]->(c:TaekwondoChunk)
            WITH v, nodes, edges, count(DISTINCT c) AS chunks
            OPTIONAL MATCH (v)-[:ASSERTS_FACT]->(f:TaekwondoKnowledgeFact)
            RETURN nodes, edges, chunks, count(DISTINCT f) AS facts
            """,
            version_id=version_id,
        )
        if not rows:
            return None
        return {
            "commitStatus": "COMMITTED",
            **{key: int(rows[0][key]) for key in ("nodes", "edges", "chunks", "facts")},
            "readbackVerified": True,
            "reconciled": True,
        }

    async def is_graphrag_complete(
        self, version_id: str, expected_chunks: int
    ) -> bool:
        """Verify embeddings and provenance before a resumable backfill skips a version."""

        rows = await self._data(
            """
            MATCH (v:TaekwondoSourceVersion {versionId: $version_id, status: 'COMMITTED'})
            OPTIONAL MATCH (v)-[:HAS_CHUNK]->(chunk:TaekwondoChunk)
            WITH v, count(DISTINCT chunk) AS chunks,
                 count(DISTINCT CASE WHEN chunk.embedding IS NOT NULL THEN chunk END)
                   AS embeddedChunks
            OPTIONAL MATCH (v)-[:ASSERTS_ENTITY]->(entity:TaekwondoKnowledgeEntity)
            WITH v, chunks, embeddedChunks, collect(DISTINCT entity) AS entities
            OPTIONAL MATCH (v)-[:ASSERTS_FACT]->(fact:TaekwondoKnowledgeFact)
            WITH v, chunks, embeddedChunks, entities, collect(DISTINCT fact) AS facts
            RETURN chunks, embeddedChunks,
              all(entity IN entities WHERE entity IS NULL OR EXISTS {
                MATCH (entity)-[:MENTIONED_IN]->(:TaekwondoChunk {versionId: $version_id})
              }) AS entitiesGrounded,
              all(fact IN facts WHERE fact IS NULL OR (
                fact.embedding IS NOT NULL AND EXISTS {
                  MATCH (fact)-[:SUPPORTED_BY]->(:TaekwondoChunk {versionId: $version_id})
                }
              )) AS factsGrounded
            """,
            version_id=version_id,
        )
        if not rows:
            return False
        row = rows[0]
        return bool(
            int(row["chunks"]) == expected_chunks
            and int(row["embeddedChunks"]) == expected_chunks
            and row["entitiesGrounded"]
            and row["factsGrounded"]
        )

    @staticmethod
    async def _deactivate_tx(tx: Any, version_id: str, status: str) -> dict[str, Any]:
        rel_record = await (
            await tx.run(
                """
                MATCH ()-[r:TAEKWONDO_RELATION {versionId: $version_id}]->()
                DELETE r
                RETURN count(r) AS removedRelationships
                """,
                version_id=version_id,
            )
        ).single()
        await (
            await tx.run(
                """
                MATCH ()-[m:MENTIONED_IN {versionId: $version_id}]->()
                DELETE m
                """,
                version_id=version_id,
            )
        ).consume()
        record = await (
            await tx.run(
                """
                MATCH (v:TaekwondoSourceVersion {versionId: $version_id})
                SET v.status = $status, v.updatedAt = datetime()
                WITH v
                OPTIONAL MATCH (v)-[:HAS_CHUNK|ASSERTS_FACT]->(owned)
                DETACH DELETE owned
                WITH DISTINCT v
                OPTIONAL MATCH (v)-[a:ASSERTS_ENTITY]->(n:TaekwondoKnowledgeEntity)
                DELETE a
                WITH v, collect(DISTINCT n) AS candidates
                UNWIND candidates AS candidate
                WITH candidate
                WHERE NOT (:TaekwondoSourceVersion {status: 'COMMITTED'})
                          -[:ASSERTS_ENTITY]->(candidate)
                DETACH DELETE candidate
                RETURN count(candidate) AS removedNodes
                """,
                version_id=version_id,
                status=status,
            )
        ).single()
        return {
            "versionId": version_id,
            "status": status,
            "removedNodes": int(record["removedNodes"] if record else 0),
            "removedRelationships": int(
                rel_record["removedRelationships"] if rel_record else 0
            ),
        }

    async def purge_staging(self, ingestion_id: str) -> None:
        async with self._driver.session(database=self._database) as session:
            await (
                await session.run(
                    "MATCH (b:TaekwondoIngestionBatch {ingestionId: $id}) DETACH DELETE b",
                    id=ingestion_id,
                )
            ).consume()


async def _build_graphrag_payload(
    workspace: Workspace,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    embedding_provider: GeminiEmbeddingProvider,
) -> dict[str, Any]:
    version_id = str(workspace.version.id)
    document_id = str(workspace.document.id)
    chunk_id_by_index = {item.chunk_index: item.chunk_id for item in workspace.chunks}
    scope_by_chunk_index = {
        chunk_index: batch.scope_key
        for batch in workspace.batches
        for chunk_index in batch.chunk_indexes
    }
    chunks = [
        {
            "chunkId": item.chunk_id,
            "chunkIndex": item.chunk_index,
            "text": item.text,
            "contentHash": item.content_hash,
            "tokenCount": item.token_count,
            "section": item.section,
            "pageStart": item.page_start,
            "pageEnd": item.page_end,
            "sourceAnchor": item.source_anchor,
            "scopeKey": scope_by_chunk_index.get(item.chunk_index, "core"),
        }
        for item in workspace.chunks
    ]
    chunk_vectors = await embedding_provider.embed_documents([item["text"] for item in chunks])
    for chunk, vector in zip(chunks, chunk_vectors, strict=True):
        chunk["embedding"] = vector

    normalized_nodes: list[dict[str, Any]] = []
    node_by_temp: dict[str, dict[str, Any]] = {}
    mentions: dict[tuple[str, str], set[str]] = {}
    facts: list[dict[str, Any]] = []
    for node in nodes:
        stable_key = _stable_entity_key(node, version_id)
        identity = node.get("identity") or {}
        properties = {
            item["propertyName"]: item.get("value")
            for item in node.get("properties", [])
        }
        normalized = {
            "tempId": node["tempId"],
            "stableKey": stable_key,
            "className": node["className"],
            "identityJson": _json(identity),
            "propertiesJson": _json(properties),
            "searchText": _search_text(node["className"], identity, properties),
        }
        normalized_nodes.append(normalized)
        node_by_temp[node["tempId"]] = normalized
        evidence = list(node.get("evidence") or [])
        for prop in node.get("properties", []):
            evidence.extend(prop.get("evidence") or [])
            statement = (
                f"{normalized['searchText']} | {prop['propertyName']}: "
                f"{_display_value(prop.get('value'))}"
            )
            facts.append(
                _fact(
                    version_id,
                    "PROPERTY",
                    statement,
                    prop["propertyName"],
                    prop.get("value"),
                    stable_key,
                    None,
                    prop.get("evidence") or [],
                    chunk_id_by_index,
                    node.get("confidence"),
                )
            )
        _collect_mentions(mentions, stable_key, evidence, chunk_id_by_index)

    normalized_edges: list[dict[str, Any]] = []
    for edge in edges:
        source = node_by_temp[edge["sourceTempId"]]
        target = node_by_temp[edge["targetTempId"]]
        fact_text = (
            f"{source['searchText']} --{edge['edgeName']}--> {target['searchText']}"
        )
        if edge.get("properties"):
            fact_text += f" | {_json(edge['properties'])}"
        normalized_edges.append(
            {
                "edgeName": edge["edgeName"],
                "sourceKey": source["stableKey"],
                "targetKey": target["stableKey"],
                "propertiesJson": _json(edge.get("properties") or {}),
                "evidenceJson": _json(edge.get("evidence") or []),
                "factText": fact_text,
            }
        )
        facts.append(
            _fact(
                version_id,
                "RELATIONSHIP",
                fact_text,
                edge["edgeName"],
                edge.get("properties") or {},
                source["stableKey"],
                target["stableKey"],
                edge.get("evidence") or [],
                chunk_id_by_index,
                edge.get("confidence"),
            )
        )

    if facts:
        fact_vectors = await embedding_provider.embed_documents(
            [item["statement"] for item in facts]
        )
        for fact, vector in zip(facts, fact_vectors, strict=True):
            fact["embedding"] = vector

    ordered_chunks = sorted(chunks, key=lambda item: item["chunkIndex"])
    return {
        "version_id": version_id,
        "document_id": document_id,
        "document_name": workspace.document.name,
        "ontology_version_id": str(workspace.version.ontology_version_id),
        "embedding_model": embedding_provider.model,
        "chunks": ordered_chunks,
        "chunk_links": [
            {"from": left["chunkId"], "to": right["chunkId"]}
            for left, right in pairwise(ordered_chunks)
        ],
        "nodes": normalized_nodes,
        "mentions": [
            {"stableKey": stable_key, "chunkId": chunk_id, "quotes": sorted(quotes)}
            for (stable_key, chunk_id), quotes in mentions.items()
        ],
        "edges": normalized_edges,
        "facts": facts,
    }


def _merge_fragments(fragments: list[dict]) -> tuple[list[dict], list[dict]]:
    nodes_by_key: dict[tuple[str, str], dict] = {}
    fragment_temp_maps: list[dict[str, str]] = []
    for fragment in fragments:
        temp_map: dict[str, str] = {}
        for node in fragment.get("nodes", []):
            identity_json = _json(node.get("identity") or {"tempId": node["tempId"]})
            key = (node["className"], identity_json)
            existing = nodes_by_key.get(key)
            if existing is None:
                existing = {
                    **node,
                    "properties": list(node.get("properties", [])),
                    "evidence": list(node.get("evidence", [])),
                }
                nodes_by_key[key] = existing
            else:
                known = {item["propertyName"] for item in existing.get("properties", [])}
                existing["properties"].extend(
                    item
                    for item in node.get("properties", [])
                    if item["propertyName"] not in known
                )
                existing["evidence"].extend(node.get("evidence", []))
            temp_map[node["tempId"]] = existing["tempId"]
        fragment_temp_maps.append(temp_map)
    nodes = list(nodes_by_key.values())
    edges_by_key: dict[tuple[str, str, str], dict] = {}
    for fragment, temp_map in zip(fragments, fragment_temp_maps, strict=True):
        for edge in fragment.get("edges", []):
            source = temp_map.get(edge["sourceTempId"], edge["sourceTempId"])
            target = temp_map.get(edge["targetTempId"], edge["targetTempId"])
            normalized = {**edge, "sourceTempId": source, "targetTempId": target}
            edges_by_key[(edge["edgeName"], source, target)] = normalized
    return nodes, list(edges_by_key.values())


def _fact(
    version_id: str,
    fact_type: str,
    statement: str,
    predicate: str,
    value: Any,
    subject_key: str,
    object_key: str | None,
    evidence: list[dict[str, Any]],
    chunk_id_by_index: dict[int, str],
    confidence: float | None,
) -> dict[str, Any]:
    fact_id = hashlib.sha256(
        _json(
            {
                "version": version_id,
                "type": fact_type,
                "subject": subject_key,
                "predicate": predicate,
                "object": object_key,
                "value": value,
            }
        ).encode("utf-8")
    ).hexdigest()
    return {
        "factId": fact_id,
        "factType": fact_type,
        "statement": statement,
        "predicate": predicate,
        "valueJson": _json(value),
        "subjectKey": subject_key,
        "objectKey": object_key,
        "evidenceChunkIds": sorted(
            {
                chunk_id_by_index[item["chunkIndex"]]
                for item in evidence
                if item.get("chunkIndex") in chunk_id_by_index
            }
        ),
        "confidence": confidence,
    }


def _collect_mentions(
    mentions: dict[tuple[str, str], set[str]],
    stable_key: str,
    evidence: Iterable[dict[str, Any]],
    chunk_id_by_index: dict[int, str],
) -> None:
    for item in evidence:
        chunk_id = chunk_id_by_index.get(item.get("chunkIndex"))
        if chunk_id:
            mentions.setdefault((stable_key, chunk_id), set()).add(str(item.get("text", "")))


def _stable_entity_key(node: dict[str, Any], version_id: str) -> str:
    identity = node.get("identity") or {
        "versionId": version_id,
        "tempId": node["tempId"],
    }
    return hashlib.sha256(
        _json(
            {
                "class": node["className"],
                "identity": identity,
            }
        ).encode("utf-8")
    ).hexdigest()


def _search_text(class_name: str, identity: dict[str, Any], properties: dict[str, Any]) -> str:
    values = [class_name]
    values.extend(_flatten_text(identity))
    values.extend(_flatten_text(properties))
    return " | ".join(dict.fromkeys(value for value in values if value.strip()))


def _flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            result.append(str(key))
            result.extend(_flatten_text(item))
        return result
    if isinstance(value, (list, tuple, set)):
        return [text for item in value for text in _flatten_text(item)]
    return [str(value)]


def _display_value(value: Any) -> str:
    return value if isinstance(value, str) else _json(value)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _pick(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: source[key] for key in keys}


__all__ = [
    "CHUNK_FULLTEXT_INDEX",
    "CHUNK_VECTOR_INDEX",
    "ENTITY_FULLTEXT_INDEX",
    "FACT_VECTOR_INDEX",
    "Neo4jIngestionStore",
]
