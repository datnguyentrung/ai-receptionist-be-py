"""Neo4j staging and verified domain-graph promotion."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from neo4j import AsyncDriver

from app.services.ingestion.repository import Workspace


class Neo4jIngestionStore:
    """Kho lưu trữ và thao tác đồ thị tri thức Taekwondo trên Neo4j.

    Trách nhiệm:
        - Lưu trữ các batch đang ở trạng thái nháp/tạm thời (Staging nodes).
        - Khi commit (`fill`): Chuyển hóa các thực thể tri thức (`TaekwondoKnowledgeEntity`),
          quan hệ (`TaekwondoKnowledgeRelation`) và gắn liên kết với phiên bản tài liệu nguồn
          (`TaekwondoSourceVersion`) để phục vụ truy vết (Provenance).
        - Thực hiện truy vấn đọc kiểm chứng (Read-back verification) để đảm bảo dữ liệu ghi thành công.
    """

    def __init__(self, driver: AsyncDriver, database: str) -> None:
        self._driver = driver
        self._database = database

    async def stage_batch(self, ingestion_id: str, batch_index: int, fragment: dict) -> None:
        """Lưu tạm mảnh đồ thị của một batch vào node TaekwondoIngestionBatch trên Neo4j."""
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
        """Ghi chính thức toàn bộ tri thức của Workspace vào Neo4j và đọc kiểm chứng."""
        # 1. Hợp nhất các mảnh graph fragment từ tất cả các batch
        fragments = [item.graph_fragment for item in workspace.batches if item.graph_fragment]
        nodes, edges = _merge_fragments(fragments)
        ingestion_id = str(workspace.job.id)
        version_id = str(workspace.version.id)

        # 2. Thực thi transaction ghi thực thể, quan hệ và liên kết nguồn gốc
        async with self._driver.session(database=self._database) as session:
            result = await session.execute_write(
                self._write_and_verify,
                ingestion_id,
                version_id,
                str(workspace.document.id),
                nodes,
                edges,
            )
        return result

    @staticmethod
    async def _write_and_verify(tx, ingestion_id, version_id, document_id, nodes, edges):
        for node in nodes:
            identity = node.get("identity") or {}
            stable_key = hashlib.sha256(
                json.dumps(
                    {"class": node["className"], "identity": identity or node["tempId"]},
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            properties = {
                item["propertyName"]: item.get("value") for item in node.get("properties", [])
            }
            await (
                await tx.run(
                    """
                    MERGE (n:TaekwondoKnowledgeEntity {stableKey: $stable_key})
                    ON CREATE SET n.createdAt = datetime()
                    SET n.entityType = $entity_type, n.identityJson = $identity_json,
                        n.propertiesJson = $properties_json, n.updatedAt = datetime()
                    MERGE (v:TaekwondoSourceVersion {versionId: $version_id})
                    SET v.documentId = $document_id, v.status = 'WRITTEN',
                        v.updatedAt = datetime()
                    MERGE (v)-[:ASSERTS_ENTITY {tempId: $temp_id}]->(n)
                    """,
                    stable_key=stable_key,
                    entity_type=node["className"],
                    identity_json=json.dumps(identity, ensure_ascii=False, sort_keys=True),
                    properties_json=json.dumps(properties, ensure_ascii=False, sort_keys=True),
                    version_id=version_id,
                    document_id=document_id,
                    temp_id=node["tempId"],
                )
            ).consume()
            node["_stableKey"] = stable_key

        node_by_temp = {item["tempId"]: item for item in nodes}
        for edge in edges:
            source = node_by_temp[edge["sourceTempId"]]["_stableKey"]
            target = node_by_temp[edge["targetTempId"]]["_stableKey"]
            await (
                await tx.run(
                    """
                    MATCH (s:TaekwondoKnowledgeEntity {stableKey: $source})
                    MATCH (t:TaekwondoKnowledgeEntity {stableKey: $target})
                    MERGE (s)-[r:TAEKWONDO_RELATION {
                      relationType: $relation_type, versionId: $version_id,
                      sourceKey: $source, targetKey: $target
                    }]->(t)
                    SET r.propertiesJson = $properties_json,
                        r.evidenceJson = $evidence_json, r.updatedAt = datetime()
                    """,
                    source=source,
                    target=target,
                    relation_type=edge["edgeName"],
                    version_id=version_id,
                    properties_json=json.dumps(edge.get("properties") or {}, ensure_ascii=False),
                    evidence_json=json.dumps(edge.get("evidence") or [], ensure_ascii=False),
                )
            ).consume()

        record = await (
            await tx.run(
                """
                MATCH (v:TaekwondoSourceVersion {versionId: $version_id})
                OPTIONAL MATCH (v)-[:ASSERTS_ENTITY]->(n:TaekwondoKnowledgeEntity)
                WITH v, count(DISTINCT n) AS nodes
                OPTIONAL MATCH ()-[r:TAEKWONDO_RELATION {versionId: $version_id}]->()
                SET v.status = 'COMMITTED', v.committedAt = datetime()
                RETURN nodes, count(DISTINCT r) AS edges
                """,
                version_id=version_id,
            )
        ).single()
        actual_nodes = int(record["nodes"] if record else 0)
        actual_edges = int(record["edges"] if record else 0)
        if actual_nodes != len(nodes) or actual_edges != len(edges):
            raise RuntimeError(
                f"Neo4j read-back mismatch: expected {len(nodes)}/{len(edges)}, "
                f"got {actual_nodes}/{actual_edges}"
            )
        return {
            "commitStatus": "COMMITTED",
            "nodes": actual_nodes,
            "edges": actual_edges,
            "readbackVerified": True,
        }

    async def deactivate_version(self, version_id: str, status: str) -> dict[str, Any]:
        async with self._driver.session(database=self._database) as session:
            record = await session.execute_write(self._deactivate_tx, version_id, status)
        return record

    async def committed_summary(self, version_id: str) -> dict[str, Any] | None:
        """Read the durable Neo4j marker used for cross-store reconciliation."""
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MATCH (v:TaekwondoSourceVersion {versionId: $version_id, status: 'COMMITTED'})
                OPTIONAL MATCH (v)-[:ASSERTS_ENTITY]->(n:TaekwondoKnowledgeEntity)
                WITH v, count(DISTINCT n) AS nodes
                OPTIONAL MATCH ()-[r:TAEKWONDO_RELATION {versionId: $version_id}]->()
                RETURN nodes, count(DISTINCT r) AS edges
                """,
                version_id=version_id,
            )
            record = await result.single()
        if record is None:
            return None
        return {
            "commitStatus": "COMMITTED",
            "nodes": int(record["nodes"]),
            "edges": int(record["edges"]),
            "readbackVerified": True,
            "reconciled": True,
        }

    @staticmethod
    async def _deactivate_tx(tx, version_id: str, status: str) -> dict[str, Any]:
        relationships = await tx.run(
            """
            MATCH ()-[r:TAEKWONDO_RELATION {versionId: $version_id}]->()
            DELETE r
            RETURN count(r) AS removedRelationships
            """,
            version_id=version_id,
        )
        relationship_record = await relationships.single()
        result = await tx.run(
            """
            MATCH (v:TaekwondoSourceVersion {versionId: $version_id})
            SET v.status = $status, v.updatedAt = datetime()
            WITH v
            OPTIONAL MATCH (v)-[a:ASSERTS_ENTITY]->(n:TaekwondoKnowledgeEntity)
            DELETE a
            WITH v, collect(DISTINCT n) AS candidates
            UNWIND candidates AS candidate
            WITH v, candidate
            WHERE NOT (:TaekwondoSourceVersion {status: 'COMMITTED'})-[:ASSERTS_ENTITY]->(candidate)
            DETACH DELETE candidate
            RETURN count(candidate) AS removedNodes
            """,
            version_id=version_id,
            status=status,
        )
        record = await result.single()
        return {
            "versionId": version_id,
            "status": status,
            "removedNodes": int(record["removedNodes"] if record else 0),
            "removedRelationships": int(
                relationship_record["removedRelationships"] if relationship_record else 0
            ),
        }

    async def purge_staging(self, ingestion_id: str) -> None:
        async with self._driver.session(database=self._database) as session:
            async def delete_staging(tx: Any) -> None:
                result = await tx.run(
                    "MATCH (b:TaekwondoIngestionBatch {ingestionId: $id}) DETACH DELETE b",
                    id=ingestion_id,
                )
                await result.consume()

            await session.execute_write(delete_staging)


def _merge_fragments(fragments: list[dict]) -> tuple[list[dict], list[dict]]:
    nodes_by_key: dict[tuple[str, str], dict] = {}
    temp_map: dict[str, str] = {}
    for fragment in fragments:
        for node in fragment.get("nodes", []):
            identity_json = json.dumps(node.get("identity") or {"tempId": node["tempId"]}, sort_keys=True, ensure_ascii=False)
            key = (node["className"], identity_json)
            existing = nodes_by_key.get(key)
            if existing is None:
                existing = {**node, "properties": list(node.get("properties", []))}
                nodes_by_key[key] = existing
            else:
                known = {item["propertyName"] for item in existing.get("properties", [])}
                existing["properties"].extend(
                    item for item in node.get("properties", []) if item["propertyName"] not in known
                )
            temp_map[node["tempId"]] = existing["tempId"]
    nodes = list(nodes_by_key.values())
    edges_by_key: dict[tuple[str, str, str], dict] = {}
    for fragment in fragments:
        for edge in fragment.get("edges", []):
            source = temp_map.get(edge["sourceTempId"], edge["sourceTempId"])
            target = temp_map.get(edge["targetTempId"], edge["targetTempId"])
            normalized = {**edge, "sourceTempId": source, "targetTempId": target}
            edges_by_key[(edge["edgeName"], source, target)] = normalized
    return nodes, list(edges_by_key.values())


__all__ = ["Neo4jIngestionStore"]
