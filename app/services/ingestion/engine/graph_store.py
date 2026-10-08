"""Lưu trữ và truy vấn tri thức đồ thị (Knowledge Graph) trên Neo4j phục vụ Taekwondo GraphRAG.

Module này cung cấp các thao tác tương tác với cơ sở dữ liệu đồ thị Neo4j:
- Tạo và đảm bảo các chỉ mục vector (vector index) và toàn văn (fulltext index).
- Ghi nguyên tử toàn bộ đồ thị thực thể, quan hệ, sự kiện (facts), vector embeddings và liên kết trích dẫn.
- Tìm kiếm vector tương đồng (chunk vector, fact vector) và tìm kiếm toàn văn (fulltext search).
- Vô hiệu hóa hoặc hủy bỏ các phiên bản tài liệu đã cũ/rollback.

Danh sách các hàm / phương thức trong module:
- `Neo4jIngestionStore.__init__(...)`: Khởi tạo store với AsyncDriver, tên database và embedding provider.
- `Neo4jIngestionStore.ensure_indexes(...)`: Tạo các chỉ mục vector, fulltext và ràng buộc duy nhất trên Neo4j.
- `Neo4jIngestionStore.fill(...)`: Ghi dữ liệu đồ thị, chunks, facts, embeddings và provenance một cách nguyên tử.
- `Neo4jIngestionStore._write_and_verify(...)`: Thực thi giao dịch Cypher ghi dữ liệu và đối soát số lượng nút/cạnh.
- `Neo4jIngestionStore.search_chunk_vector(...)`: Tìm kiếm chunk theo độ tương đồng vector embedding.
- `Neo4jIngestionStore.search_fact_vector(...)`: Tìm kiếm fact theo độ tương đồng vector embedding.
- `Neo4jIngestionStore.search_chunk_fulltext(...)`: Tìm kiếm chunk bằng chỉ mục toàn văn (fulltext).
- `Neo4jIngestionStore.search_entity_fulltext(...)`: Tìm kiếm thực thể bằng chỉ mục toàn văn.
- `Neo4jIngestionStore.chunks_for_entities(...)`: Lấy các chunk liên quan đến danh sách thực thể qua quan hệ đồ thị.
- `Neo4jIngestionStore.chunks_by_ids(...)`: Lấy thông tin các chunk theo danh sách chunkId.
- `Neo4jIngestionStore._data(...)`: Thực thi câu lệnh Cypher và trả về kết quả dưới dạng danh sách từ điển.
- `Neo4jIngestionStore.deactivate_version(...)`: Vô hiệu hóa một phiên bản tài liệu trong Neo4j.
- `Neo4jIngestionStore.committed_summary(...)`: Đọc lại số lượng thực thể/quan hệ đã commit của một version.
- `Neo4jIngestionStore.is_graphrag_complete(...)`: Kiểm tra phiên bản đã nạp đầy đủ embeddings và trích dẫn chưa.
- `Neo4jIngestionStore._deactivate_tx(...)`: Thực thi giao dịch xóa/vô hiệu hóa các quan hệ và thực thể mồ côi.
- `Neo4jIngestionStore.purge_staging(...)`: Xóa bỏ các nút batch tạm thời của phiên nạp.
- `_build_graphrag_payload(...)`: Xây dựng dữ liệu payload đầy đủ bao gồm embeddings phục vụ ghi vào Neo4j.
- `_merge_fragments(...)`: Hợp nhất danh sách các graph fragment từ nhiều batch thành tập nút và cạnh duy nhất.
- `_fact(...)`: Tạo đối tượng dữ liệu sự kiện (fact) kèm mã băm định danh và danh sách chunk hỗ trợ.
- `_collect_mentions(...)`: Thu thập các trích dẫn đề cập (mentions) giữa thực thể và chunk.
- `_stable_entity_key(...)`: Tính mã băm định danh duy nhất cho thực thể theo className và identity.
- `_search_text(...)`: Tạo chuỗi văn bản tìm kiếm tổng hợp cho thực thể.
- `_flatten_text(...)`: Chuyển đổi cấu trúc dữ liệu lồng nhau thành danh sách các chuỗi văn bản.
- `_display_value(...)`: Định dạng giá trị thuộc tính thành chuỗi hiển thị.
- `_json(...)`: Chuyển đổi dữ liệu sang chuỗi JSON chuẩn hóa ổn định.
- `_pick(...)`: Trích xuất một tập khóa con từ từ điển nguồn.
"""

import hashlib
import json
from collections.abc import Iterable
from itertools import pairwise
from typing import Any, cast

from neo4j import AsyncDriver

from app.services.graphrag.embeddings import GeminiEmbeddingProvider
from app.services.ingestion.engine.repository import Workspace

CHUNK_VECTOR_INDEX = "taekwondo_chunk_embedding"
FACT_VECTOR_INDEX = "taekwondo_fact_embedding"
CHUNK_FULLTEXT_INDEX = "taekwondo_chunk_text"
ENTITY_FULLTEXT_INDEX = "taekwondo_entity_search_text"


class Neo4jIngestionStore:
    """
    Lớp lưu trữ và truy vấn tri thức đồ thị trên cơ sở dữ liệu Neo4j cho hệ thống Taekwondo GraphRAG.
    """

    def __init__(
        self,
        driver: AsyncDriver,
        database: str,
        *,
        embedding_provider: GeminiEmbeddingProvider,
    ) -> None:
        """
        Khởi tạo Neo4jIngestionStore.

        Args:
            driver: Đối tượng AsyncDriver kết nối Neo4j.
            database: Tên cơ sở dữ liệu Neo4j mục tiêu.
            embedding_provider: Bộ tạo vector embedding (Gemini).
        """
        # 1. Lưu driver kết nối Neo4j
        self._driver = driver
        # 2. Lưu tên database
        self._database = database
        # 3. Lưu embedding provider
        self._embedding_provider = embedding_provider

    async def ensure_indexes(self, embedding_dimension: int) -> None:
        """
        Tạo các chỉ mục ngữ nghĩa (vector, fulltext) và ràng buộc duy nhất trên Neo4j một cách idempotent.

        Args:
            embedding_dimension: Số chiều của vector embedding (ví dụ 768 hoặc 1536).

        Raises:
            RuntimeError: Nếu phát hiện bản ghi trùng lặp trước khi tạo ràng buộc UNIQUE.
        """
        # 1. Định nghĩa các câu lệnh tạo vector index và fulltext index
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
            # 2. Thực thi tạo các index
            for statement in statements:
                await (await session.run(cast(Any, statement))).consume()

            # 3. Kiểm tra tính duy nhất trước khi tạo ràng buộc UNIQUE
            duplicate_checks = {
                "TaekwondoKnowledgeEntity.stableKey": """
                    MATCH (n:TaekwondoKnowledgeEntity)
                    WITH n.stableKey AS value, count(*) AS total
                    WHERE value IS NOT NULL AND total > 1
                    RETURN count(*) AS duplicates
                """,
                "TaekwondoKnowledgeFact.factId": """
                    MATCH (n:TaekwondoKnowledgeFact)
                    WITH n.factId AS value, count(*) AS total
                    WHERE value IS NOT NULL AND total > 1
                    RETURN count(*) AS duplicates
                """,
                "TaekwondoSourceVersion.versionId": """
                    MATCH (n:TaekwondoSourceVersion)
                    WITH n.versionId AS value, count(*) AS total
                    WHERE value IS NOT NULL AND total > 1
                    RETURN count(*) AS duplicates
                """,
            }
            for label, query in duplicate_checks.items():
                record = await (await session.run(cast(Any, query))).single()
                if record and int(record["duplicates"] or 0):
                    raise RuntimeError(
                        f"Cannot create uniqueness constraint; duplicates exist for {label}"
                    )

            # 4. Tạo các ràng buộc tính duy nhất
            constraints = (
                "CREATE CONSTRAINT taekwondo_entity_stable_key_unique IF NOT EXISTS "
                "FOR (n:TaekwondoKnowledgeEntity) REQUIRE n.stableKey IS UNIQUE",
                "CREATE CONSTRAINT taekwondo_fact_id_unique IF NOT EXISTS "
                "FOR (n:TaekwondoKnowledgeFact) REQUIRE n.factId IS UNIQUE",
                "CREATE CONSTRAINT taekwondo_source_version_id_unique IF NOT EXISTS "
                "FOR (n:TaekwondoSourceVersion) REQUIRE n.versionId IS UNIQUE",
            )
            for statement in constraints:
                await (await session.run(cast(Any, statement))).consume()

    async def fill(self, workspace: Workspace) -> dict[str, Any]:
        """
        Ghi chính thức đồ thị tri thức, chunks, facts, embeddings và provenance vào Neo4j trong một giao dịch nguyên tử.

        Args:
            workspace: Đối tượng Workspace chứa dữ liệu đã sẵn sàng ghi.

        Returns:
            dict[str, Any]: Kết quả xác nhận số lượng nút, cạnh, facts và chunks đã ghi vào Neo4j.
        """
        # 1. Thu thập tất cả graph fragment từ các batch
        fragments = [
            item.graph_fragment for item in workspace.batches if item.graph_fragment
        ]
        # 2. Hợp nhất các fragment thành tập nodes và edges hoàn chỉnh
        nodes, edges = _merge_fragments(fragments)
        # 3. Tạo payload GraphRAG kèm vector embeddings
        payload = await _build_graphrag_payload(
            workspace,
            nodes,
            edges,
            self._embedding_provider,
        )
        # 4. Thực thi ghi dữ liệu và đối soát trong session Neo4j
        async with self._driver.session(database=self._database) as session:
            return await session.execute_write(self._write_and_verify, payload)

    @staticmethod
    async def _write_and_verify(tx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Thực thi các câu lệnh Cypher ghi dữ liệu và kiểm tra đối soát số lượng bản ghi thực tế trong Neo4j.

        Args:
            tx: Đối tượng giao dịch Neo4j Transaction.
            payload: Từ điển dữ liệu nạp đồ thị.

        Returns:
            dict[str, Any]: Thống kê số lượng bản ghi đã được xác minh đối soát.

        Raises:
            RuntimeError: Nếu số lượng đọc lại không khớp với số lượng trong payload.
        """
        # 1. Ghi thông tin phiên bản tài liệu (TaekwondoSourceVersion)
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

        # 2. Ghi các đoạn văn bản (TaekwondoChunk) kèm vector embeddings
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
                    c.scopeKeys = chunk.scopeKeys,
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

        # 3. Tạo liên kết chuỗi NEXT_CHUNK giữa các chunk liền kề
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

        # 4. Ghi các thực thể tri thức (TaekwondoKnowledgeEntity)
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

        # 5. Ghi các trích dẫn đề cập MENTIONED_IN giữa thực thể và chunk
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

        # 6. Ghi các mối quan hệ đồ thị TAEKWONDO_RELATION
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

        # 7. Ghi các sự kiện tri thức (TaekwondoKnowledgeFact) và vector embedding
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

        # 8. Đọc lại và đối soát toàn diện số lượng bản ghi đã commit
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
            raise RuntimeError(
                f"Neo4j read-back mismatch: expected {expected}, got {actual}"
            )
        return {"commitStatus": "COMMITTED", **actual, "readbackVerified": True}

    async def search_chunk_vector(
        self, embedding: list[float], limit: int
    ) -> list[dict[str, Any]]:
        """
        Tìm kiếm các chunk có vector embedding gần nhất theo khoảng cách cosine similarity.

        Args:
            embedding: Vector embedding truy vấn.
            limit: Số lượng kết quả tối đa cần lấy.

        Returns:
            list[dict[str, Any]]: Danh sách các chunk kèm điểm số tương đồng (score).
        """
        if not embedding or limit <= 0:
            return []
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
                   node.scopeKey AS scopeKey, node.scopeKeys AS scopeKeys,
                   node.embedding AS embedding, score
            ORDER BY score DESC
            LIMIT $limit
            """,
            index=CHUNK_VECTOR_INDEX,
            limit=limit,
            embedding=embedding,
        )

    async def search_fact_vector(
        self, embedding: list[float], limit: int
    ) -> list[dict[str, Any]]:
        """
        Tìm kiếm các sự kiện tri thức (facts) tương đồng nhất dựa trên vector embedding.

        Args:
            embedding: Vector embedding truy vấn.
            limit: Số lượng kết quả tối đa cần lấy.

        Returns:
            list[dict[str, Any]]: Danh sách các fact kèm điểm số tương đồng (score).
        """
        if not embedding or limit <= 0:
            return []
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
            LIMIT $limit
            """,
            index=FACT_VECTOR_INDEX,
            limit=limit,
            embedding=embedding,
        )

    async def search_chunk_fulltext(
        self, query: str, limit: int
    ) -> list[dict[str, Any]]:
        """
        Tìm kiếm toàn văn (fulltext) trên nội dung các đoạn văn bản (chunks).

        Args:
            query: Từ khóa tìm kiếm Lucene.
            limit: Số lượng kết quả tối đa.

        Returns:
            list[dict[str, Any]]: Danh sách các chunk khớp nội dung tìm kiếm.
        """
        if not query or not query.strip() or limit <= 0:
            return []
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
                   node.scopeKey AS scopeKey, node.scopeKeys AS scopeKeys,
                   node.embedding AS embedding, score
            ORDER BY score DESC
            LIMIT $limit
            """,
            index=CHUNK_FULLTEXT_INDEX,
            query=query,
            limit=limit,
        )

    async def search_entity_fulltext(
        self, query: str, limit: int
    ) -> list[dict[str, Any]]:
        """
        Tìm kiếm toàn văn (fulltext) trên văn bản đại diện của các thực thể tri thức.

        Args:
            query: Từ khóa tìm kiếm.
            limit: Số lượng kết quả tối đa.

        Returns:
            list[dict[str, Any]]: Danh sách thực thể khớp tìm kiếm.
        """
        if not query or not query.strip() or limit <= 0:
            return []
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
            LIMIT $limit
            """,
            index=ENTITY_FULLTEXT_INDEX,
            query=query,
            limit=limit,
        )

    async def chunks_for_entities(
        self, stable_keys: list[str], limit: int
    ) -> list[dict[str, Any]]:
        """
        Tìm các chunks chứa bằng chứng liên quan đến danh sách thực thể thông qua duyệt đồ thị 0-2 bước.

        Args:
            stable_keys: Danh sách stable key của các thực thể.
            limit: Số lượng chunk tối đa cần lấy.

        Returns:
            list[dict[str, Any]]: Danh sách các chunk liên quan.
        """
        if not stable_keys or limit <= 0:
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
                   chunk.scopeKey AS scopeKey, chunk.scopeKeys AS scopeKeys,
                   chunk.embedding AS embedding,
                   1.0 / (1.0 + length(path)) AS score
            ORDER BY score DESC
            LIMIT $limit
            """,
            stable_keys=stable_keys,
            limit=limit,
        )

    async def chunks_by_ids(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        """
        Lấy thông tin chi tiết các chunk theo danh sách chunkId.

        Args:
            chunk_ids: Danh sách mã định danh chunk.

        Returns:
            list[dict[str, Any]]: Danh sách chunk tìm thấy.
        """
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
                   chunk.scopeKey AS scopeKey, chunk.scopeKeys AS scopeKeys,
                   chunk.embedding AS embedding, 1.0 AS score
            """,
            chunk_ids=chunk_ids,
        )

    async def _data(self, cypher: str, **parameters: Any) -> list[dict[str, Any]]:
        """
        Hàm nội bộ thực thi câu lệnh Cypher và trả về kết quả dưới dạng danh sách từ điển.

        Args:
            cypher: Chuỗi câu lệnh Cypher.
            **parameters: Tham số truyền vào câu lệnh.

        Returns:
            list[dict[str, Any]]: Danh sách các bản ghi kết quả.
        """
        async with self._driver.session(database=self._database) as session:
            return await (await session.run(cast(Any, cypher), **parameters)).data()

    async def deactivate_version(self, version_id: str, status: str) -> dict[str, Any]:
        """
        Vô hiệu hóa một phiên bản tài liệu trên Neo4j (xóa các quan hệ, facts, chunks và thực thể mồ côi).

        Args:
            version_id: Mã định danh phiên bản tài liệu cần vô hiệu hóa.
            status: Trạng thái mới cần cập nhật (ví dụ: 'DELETED', 'SUPERSEDED', 'ROLLED_BACK').

        Returns:
            dict[str, Any]: Thống kê số lượng nút và quan hệ đã bị gỡ bỏ.
        """
        async with self._driver.session(database=self._database) as session:
            return await session.execute_write(self._deactivate_tx, version_id, status)

    async def committed_summary(self, version_id: str) -> dict[str, Any] | None:
        """
        Đọc lại số lượng thực thể, quan hệ, chunks và facts đã commit của một version để đối soát.

        Args:
            version_id: Mã phiên bản tài liệu.

        Returns:
            dict[str, Any] | None: Báo cáo đối soát hoặc None nếu version chưa commit.
        """
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

    async def is_graphrag_complete(self, version_id: str, expected_chunks: int) -> bool:
        """
        Xác minh phiên bản tài liệu đã có đầy đủ embeddings và trích dẫn bằng chứng hợp lệ chưa.

        Args:
            version_id: Mã phiên bản tài liệu.
            expected_chunks: Số lượng chunk kỳ vọng.

        Returns:
            bool: True nếu dữ liệu GraphRAG hoàn chỉnh, ngược lại False.
        """
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
        """
        Thực thi giao dịch xóa quan hệ, facts và các thực thể mồ côi không còn version nào tham chiếu.

        Args:
            tx: Giao dịch Neo4j Transaction.
            version_id: Mã phiên bản cần vô hiệu hóa.
            status: Trạng thái mới.

        Returns:
            dict[str, Any]: Thống kê số lượng nút và quan hệ đã bị xóa.
        """
        # 1. Xóa các quan hệ đồ thị thuộc version này
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

        # 2. Xóa các liên kết MENTIONED_IN
        await (
            await tx.run(
                """
                MATCH ()-[m:MENTIONED_IN {versionId: $version_id}]->()
                DELETE m
                """,
                version_id=version_id,
            )
        ).consume()

        # 3. Xóa các chunk, facts thuộc quyền sở hữu của version và xóa các thực thể mồ côi
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
                UNWIND (CASE WHEN size(candidates) = 0 THEN [null] ELSE candidates END) AS candidate
                WITH candidate
                WHERE candidate IS NOT NULL AND NOT (:TaekwondoSourceVersion {status: 'COMMITTED'})
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
        """
        Xóa sạch các nút IngestionBatch tạm thời của phiên nạp tài liệu.

        Args:
            ingestion_id: Mã định danh phiên nạp.
        """
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
    """
    Xây dựng payload dữ liệu hoàn chỉnh bao gồm chunks, nodes, edges, facts và vector embeddings.

    Args:
        workspace: Workspace chứa chunks và metadata.
        nodes: Danh sách thực thể đã hợp nhất.
        edges: Danh sách quan hệ đã hợp nhất.
        embedding_provider: Bộ tạo vector embedding.

    Returns:
        dict[str, Any]: Từ điển payload sẵn sàng ghi vào Neo4j.
    """
    version_id = str(workspace.version.id)
    document_id = str(workspace.document.id)
    chunk_id_by_index = {item.chunk_index: item.chunk_id for item in workspace.chunks}
    scopes_by_chunk_index = {
        chunk_index: (getattr(batch, "scope_keys", None) or [])
        for batch in getattr(workspace, "batches", ())
        for chunk_index in getattr(batch, "chunk_indexes", ())
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
            "scopeKeys": scopes_by_chunk_index.get(item.chunk_index, []),
            "scopeKey": ",".join(scopes_by_chunk_index.get(item.chunk_index, [])),
        }
        for item in workspace.chunks
    ]
    # Tạo embedding cho tất cả các chunk
    chunk_vectors = await embedding_provider.embed_documents(
        [str(item["text"]) for item in chunks]
    )
    for chunk, vector in zip(chunks, chunk_vectors, strict=True):
        chunk["embedding"] = vector

    normalized_nodes: list[dict[str, Any]] = []
    node_by_temp: dict[str, dict[str, Any]] = {}
    mentions: dict[tuple[str, str], set[str]] = {}
    facts: list[dict[str, Any]] = []

    # Xử lý chuẩn hóa các nút và tạo fact tương ứng
    for node in nodes:
        stable_key = _stable_entity_key(node, version_id)
        identity = node.get("identity") or {}
        properties: dict[str, Any] = {}
        for item in node.get("properties", []):
            name = item["propertyName"]
            value = item.get("value")
            if name not in properties:
                properties[name] = value
            elif properties[name] != value:
                current = properties[name]
                values = current if isinstance(current, list) else [current]
                if value not in values:
                    values.append(value)
                properties[name] = values
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

    # Xử lý chuẩn hóa các cạnh quan hệ và tạo fact tương ứng
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

    # Tạo vector embedding cho tất cả các fact
    if facts:
        fact_vectors = await embedding_provider.embed_documents(
            [str(item["statement"]) for item in facts]
        )
        for fact, vector in zip(facts, fact_vectors, strict=True):
            fact["embedding"] = vector

    def _get_chunk_index(item: dict[str, Any]) -> int:
        return int(str(item.get("chunkIndex") or 0))

    ordered_chunks = sorted(chunks, key=_get_chunk_index)
    return {
        "version_id": version_id,
        "document_id": document_id,
        "document_name": getattr(workspace.document, "name", ""),
        "ontology_version_id": str(
            getattr(workspace.version, "ontology_version_id", "")
        ),
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


def _merge_fragments(
    fragments: list[dict | Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Hợp nhất danh sách các graph fragment từ nhiều batch thành tập nút và cạnh duy nhất.

    Args:
        fragments: Danh sách các mảnh đồ thị fragment.

    Returns:
        tuple[list[dict[str, Any]], list[dict[str, Any]]]: Bộ đôi (danh sách nodes, danh sách edges).
    """
    nodes_by_canonical_id: dict[str, dict] = {}
    temp_id_to_canonical_id: dict[str, str] = {}
    fragment_temp_maps: list[dict[str, str]] = []
    dict_fragments: list[dict[str, Any]] = []

    for raw_fragment in fragments:
        if isinstance(raw_fragment, dict):
            fragment = raw_fragment
        elif hasattr(raw_fragment, "model_dump"):
            fragment = getattr(raw_fragment, "model_dump")(by_alias=True, mode="json")
        else:
            fragment = {}
        dict_fragments.append(fragment)
        temp_map: dict[str, str] = {}
        for node in fragment.get("nodes", []):
            orig_temp_id = node["tempId"]
            target_alias = orig_temp_id

            canonical_id = None
            identity_json = _json(node.get("identity") or {"tempId": orig_temp_id})

            if target_alias in nodes_by_canonical_id:
                canonical_id = target_alias
            else:
                for existing_id, existing_node in nodes_by_canonical_id.items():
                    if existing_node["className"] == node["className"]:
                        existing_ident_json = _json(
                            existing_node.get("identity") or {"tempId": existing_id}
                        )
                        if existing_ident_json == identity_json:
                            canonical_id = existing_id
                            break

            if canonical_id is None:
                canonical_id = target_alias
                nodes_by_canonical_id[canonical_id] = {
                    **node,
                    "tempId": canonical_id,
                    "properties": list(node.get("properties", [])),
                    "evidence": list(node.get("evidence", [])),
                }
            else:
                existing = nodes_by_canonical_id[canonical_id]
                known = {
                    (item["propertyName"], _json(item.get("value")))
                    for item in existing.get("properties", [])
                }
                existing["properties"].extend(
                    item
                    for item in node.get("properties", [])
                    if (item["propertyName"], _json(item.get("value"))) not in known
                )
                existing["evidence"].extend(node.get("evidence", []))

            temp_map[orig_temp_id] = canonical_id
            temp_id_to_canonical_id[orig_temp_id] = canonical_id
        fragment_temp_maps.append(temp_map)

    nodes = list(nodes_by_canonical_id.values())
    edges_by_key: dict[tuple[str, str, str], dict] = {}
    for fragment, temp_map in zip(dict_fragments, fragment_temp_maps, strict=True):
        for edge in fragment.get("edges", []):
            source_raw = str(edge.get("sourceTempId", ""))
            target_raw = str(edge.get("targetTempId", ""))
            source = temp_map.get(source_raw, source_raw)
            target = temp_map.get(target_raw, target_raw)
            source = temp_id_to_canonical_id.get(source, source)
            target = temp_id_to_canonical_id.get(target, target)
            normalized = {**edge, "sourceTempId": source, "targetTempId": target}
            edges_by_key[(edge.get("edgeName", ""), source, target)] = normalized
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
    """
    Tạo cấu trúc đối tượng dữ liệu sự kiện tri thức (Fact) kèm mã băm SHA-256 định danh duy nhất.
    """
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
    """
    Thu thập các trích dẫn bằng chứng đề cập giữa thực thể và chunk vào từ điển gom nhóm.
    """
    for item in evidence:
        idx = item.get("chunkIndex")
        if isinstance(idx, int):
            chunk_id = chunk_id_by_index.get(idx)
            if chunk_id:
                mentions.setdefault((stable_key, chunk_id), set()).add(
                    str(item.get("text", ""))
                )


def _stable_entity_key(node: dict[str, Any], version_id: str) -> str:
    """
    Tạo mã băm SHA-256 làm stable key cho một thực thể đồ thị.
    """
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


def _search_text(
    class_name: str, identity: dict[str, Any], properties: dict[str, Any]
) -> str:
    """
    Tạo chuỗi văn bản đại diện tổng hợp phục vụ tìm kiếm toàn văn cho một thực thể.
    """
    values = [class_name]
    values.extend(_flatten_text(identity))
    values.extend(_flatten_text(properties))
    return " | ".join(dict.fromkeys(value for value in values if value.strip()))


def _flatten_text(value: Any) -> list[str]:
    """
    Chuyển đổi cấu trúc dữ liệu phức tạp thành danh sách các chuỗi văn bản phẳng.
    """
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
    """
    Chuyển đổi giá trị thành chuỗi hiển thị.
    """
    return value if isinstance(value, str) else _json(value)


def _json(value: Any) -> str:
    """
    Chuyển đổi dữ liệu sang định dạng JSON chuẩn hóa (sort_keys=True).
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _pick(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    """
    Trích xuất một tập khóa con từ từ điển nguồn.
    """
    return {key: source[key] for key in keys}


__all__ = [
    "CHUNK_FULLTEXT_INDEX",
    "CHUNK_VECTOR_INDEX",
    "ENTITY_FULLTEXT_INDEX",
    "FACT_VECTOR_INDEX",
    "Neo4jIngestionStore",
]
