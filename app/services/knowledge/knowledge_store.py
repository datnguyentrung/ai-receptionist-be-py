"""Tenant-isolated Neo4j store for Layer 2 Knowledge Instances and Layer 1 Business Refs."""

import json
from typing import Any

from neo4j import AsyncDriver

from app.schemas.knowledge import KnowledgeFactRecord
from app.services.graphrag.embeddings import GeminiEmbeddingProvider


class TenantKnowledgeStore:
    """Persists and queries Layer 2 KnowledgeFactRecords isolated by `tenant_id`."""

    def __init__(
        self,
        driver: AsyncDriver,
        database: str,
        *,
        embedding_provider: GeminiEmbeddingProvider | None = None,
    ) -> None:
        self._driver = driver
        self._database = database
        self._embedding_provider = embedding_provider

    async def upsert_fact_record(self, record: KnowledgeFactRecord) -> dict[str, Any]:
        """Persist a Layer 2 entity, its BusinessRef links, and searchable facts for one tenant."""
        if not record.tenant_id or not record.tenant_id.strip():
            raise ValueError("tenant_id is required when persisting KnowledgeFactRecord")
        for rel in record.relationships:
            if f'"tenantId":"{record.tenant_id}"' not in rel.source_key and not rel.source_key.startswith(f"{record.tenant_id}:"):
                # FactMapper encodes tenantId inside _stable_key; guard against cross-tenant keys
                if record.tenant_id not in rel.source_key:
                    raise ValueError("Cross-tenant source_key in KnowledgeRelationshipRecord")

        embeddings: list[list[float]] = []
        if self._embedding_provider is not None and record.fact_statements:
            embeddings = await self._embedding_provider.embed_documents(
                record.fact_statements
            )

        facts_payload = [
            {
                "factId": f"{record.tenant_id}:{record.entity_key}:{idx}",
                "statement": stmt,
                "embedding": embeddings[idx] if idx < len(embeddings) else None,
            }
            for idx, stmt in enumerate(record.fact_statements)
        ]

        # Use FOREACH instead of bare UNWIND so empty business_refs or empty facts
        # never short-circuit the Cypher pipeline and drop the transaction.
        query = """
        MERGE (e:TaekwondoKnowledgeEntity {tenantId: $tenant_id, stableKey: $entity_key})
        SET e.entityType = $entity_type,
            e.scopeKey = $scope_key,
            e.scopeKeys = [$scope_key],
            e.layer = 'LAYER_2_CORE',
            e.ownership = 'CORE_KNOWLEDGE',
            e.propertiesJson = $properties_json,
            e.searchText = $search_text
        FOREACH (bref IN $business_refs |
            MERGE (r:TaekwondoBusinessRef {
                tenantId: $tenant_id,
                entityType: bref.entity_type,
                businessId: bref.business_id
            })
            SET r.layer = 'LAYER_1_BUSINESS',
                r.ownership = 'BUSINESS_API',
                r.displayName = coalesce(bref.display_name, r.displayName)
            MERGE (e)-[:REFERENCES_BUSINESS]->(r)
        )
        FOREACH (f IN $facts |
            MERGE (fact:TaekwondoKnowledgeFact {tenantId: $tenant_id, factId: f.factId})
            SET fact.statement = f.statement,
                fact.scopeKey = $scope_key,
                fact.scopeKeys = [$scope_key],
                fact.layer = 'LAYER_2_CORE',
                fact.entityKey = $entity_key,
                fact.embedding = CASE WHEN f.embedding IS NOT NULL THEN f.embedding ELSE fact.embedding END
            MERGE (e)-[:HAS_FACT]->(fact)
        )
        RETURN e.stableKey AS entityKey
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query,
                tenant_id=record.tenant_id,
                entity_key=record.entity_key,
                entity_type=record.entity_type,
                scope_key=record.scope_key,
                properties_json=json.dumps(record.properties, ensure_ascii=False),
                search_text=" ".join(record.fact_statements),
                business_refs=[ref.model_dump(mode="json") for ref in record.business_refs],
                facts=facts_payload,
            )
            row = await result.single()
            return {"entityKey": row["entityKey"] if row else record.entity_key, "facts": len(facts_payload)}

    async def list_entities(
        self,
        *,
        tenant_id: str,
        entity_type: str,
        scope_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read Layer 2 entities strictly scoped to `tenant_id`."""
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id is required when listing entities")
        query = """
        MATCH (e:TaekwondoKnowledgeEntity {tenantId: $tenant_id, entityType: $entity_type})
        WHERE $scope_key IS NULL OR e.scopeKey = $scope_key
        RETURN e.stableKey AS entityKey,
               e.entityType AS entityType,
               e.scopeKey AS scopeKey,
               e.propertiesJson AS propertiesJson
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query,
                tenant_id=tenant_id,
                entity_type=entity_type,
                scope_key=scope_key,
            )
            items: list[dict[str, Any]] = []
            async for row in result:
                props = json.loads(row["propertiesJson"]) if row["propertiesJson"] else {}
                items.append(
                    {
                        "entityKey": row["entityKey"],
                        "entityType": row["entityType"],
                        "scopeKey": row["scopeKey"],
                        "properties": props,
                    }
                )
            return items


    async def publish_governance_item(
        self,
        *,
        tenant_id: str,
        item_id: str,
        title: str,
        scope_key: str,
        facts: list[dict[str, Any]],
        visibility: str = "PUBLIC",
        effective_from: str | None = None,
        effective_to: str | None = None,
    ) -> dict[str, Any]:
        """Publish only vetted accepted_facts into Neo4j GraphRAG structures (never raw_text)."""
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id is required when publishing to Neo4j")

        fact_statements = [str(f.get("statement", "")).strip() for f in facts if f.get("statement")]
        if not fact_statements:
            raise ValueError("Cannot publish to Neo4j without vetted accepted_facts")

        sanitized_text = "\n".join(fact_statements)
        texts_to_embed = [sanitized_text] + fact_statements
        embeddings: list[list[float]] = []
        if self._embedding_provider is not None:
            embeddings = await self._embedding_provider.embed_documents(texts_to_embed)

        chunk_embedding = embeddings[0] if embeddings else None
        chunk_id = f"{tenant_id}:chunk:{item_id}"

        facts_payload = [
            {
                "factId": f"{tenant_id}:{item_id}:fact:{idx}",
                "statement": str(f.get("statement", "")),
                "entityType": str(f.get("entity_type") or "policy_rule"),
                "embedding": embeddings[idx + 1] if (idx + 1) < len(embeddings) else None,
            }
            for idx, f in enumerate(facts)
            if f.get("statement")
        ]

        query = """
        MERGE (v:TaekwondoSourceVersion {versionId: $item_id, tenantId: $tenant_id})
        SET v.status = 'COMMITTED',
            v.documentId = $item_id,
            v.documentKey = $item_id,
            v.documentName = $title,
            v.scopeKey = $scope_key,
            v.visibility = $visibility,
            v.effectiveFrom = $effective_from,
            v.effectiveTo = $effective_to,
            v.committedAt = datetime(),
            v.updatedAt = datetime()
        WITH v
        OPTIONAL MATCH (v)-[:ASSERTS_FACT]->(old_fact:TaekwondoKnowledgeFact {tenantId: $tenant_id})
        DETACH DELETE old_fact
        WITH DISTINCT v
        MERGE (c:TaekwondoChunk {chunkId: $chunk_id, tenantId: $tenant_id})
        SET c.documentId = $item_id,
            c.documentName = $title,
            c.versionId = $item_id,
            c.chunkIndex = 0,
            c.text = $sanitized_text,
            c.scopeKey = $scope_key,
            c.scopeKeys = [$scope_key],
            c.visibility = $visibility,
            c.effectiveFrom = $effective_from,
            c.effectiveTo = $effective_to,
            c.section = $title,
            c.sourceAnchor = $title,
            c.embedding = CASE WHEN $chunk_embedding IS NOT NULL THEN $chunk_embedding ELSE c.embedding END
        MERGE (v)-[:HAS_CHUNK]->(c)
        FOREACH (f IN $facts |
            MERGE (fact:TaekwondoKnowledgeFact {tenantId: $tenant_id, factId: f.factId})
            SET fact.statement = f.statement,
                fact.factType = f.entityType,
                fact.versionId = $item_id,
                fact.scopeKey = $scope_key,
                fact.scopeKeys = [$scope_key],
                fact.visibility = $visibility,
                fact.effectiveFrom = $effective_from,
                fact.effectiveTo = $effective_to,
                fact.embedding = CASE WHEN f.embedding IS NOT NULL THEN f.embedding ELSE fact.embedding END
            MERGE (v)-[:ASSERTS_FACT]->(fact)
            MERGE (fact)-[:SUPPORTED_BY]->(c)
        )
        RETURN v.versionId AS versionId
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                query,
                tenant_id=tenant_id,
                item_id=item_id,
                title=title,
                scope_key=scope_key,
                sanitized_text=sanitized_text,
                visibility=visibility,
                effective_from=effective_from,
                effective_to=effective_to,
                chunk_id=chunk_id,
                chunk_embedding=chunk_embedding,
                facts=facts_payload,
            )
            row = await result.single()
            return {
                "versionId": row["versionId"] if row else item_id,
                "chunkId": chunk_id,
                "facts": len(facts_payload),
            }

    async def delete_governance_item(self, *, tenant_id: str, item_id: str) -> int:
        """Delete a published governance item and its chunks/facts from Neo4j."""
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id is required when deleting from Neo4j")
        query = """
        MATCH (v:TaekwondoSourceVersion {versionId: $item_id, tenantId: $tenant_id})
        OPTIONAL MATCH (v)-[:HAS_CHUNK]->(c:TaekwondoChunk {tenantId: $tenant_id})
        OPTIONAL MATCH (v)-[:ASSERTS_FACT]->(f:TaekwondoKnowledgeFact {tenantId: $tenant_id})
        DETACH DELETE f, c, v
        RETURN count(v) AS deletedCount
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(query, tenant_id=tenant_id, item_id=item_id)
            row = await result.single()
            return int(row["deletedCount"]) if row else 0


__all__ = ["TenantKnowledgeStore"]
