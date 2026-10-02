import asyncio
from types import SimpleNamespace

from app.services.ingestion.graph_store import (
    Neo4jIngestionStore,
    _build_graphrag_payload,
    _merge_fragments,
)


class FakeEmbeddingProvider:
    model = "fake-embedding"

    async def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]


def test_payload_contains_chunks_facts_mentions_and_batch_scope() -> None:
    fragment = {
        "nodes": [
            {
                "tempId": "course-1",
                "className": "course",
                "identity": {"name": "Taekwondo căn bản"},
                "properties": [
                    {
                        "propertyName": "schedule",
                        "value": "Thứ Hai",
                        "evidence": [{"chunkIndex": 0, "text": "học vào thứ Hai"}],
                    }
                ],
                "evidence": [{"chunkIndex": 0, "text": "Taekwondo căn bản"}],
            }
        ],
        "edges": [],
    }
    nodes, edges = _merge_fragments([fragment])
    workspace = SimpleNamespace(
        version=SimpleNamespace(
            id="00000000-0000-0000-0000-000000000001",
            ontology_version_id="00000000-0000-0000-0000-000000000002",
        ),
        document=SimpleNamespace(
            id="00000000-0000-0000-0000-000000000003", name="course.md"
        ),
        chunks=(
            SimpleNamespace(
                chunk_id="chunk-0",
                chunk_index=0,
                text="Taekwondo căn bản học vào thứ Hai",
                content_hash="hash",
                token_count=8,
                section="Lịch học",
                page_start=None,
                page_end=None,
                source_anchor="course.md#lich-hoc",
            ),
        ),
        batches=(
            SimpleNamespace(scope_keys=["training", "finance"], chunk_indexes=[0]),
        ),
    )

    payload = asyncio.run(
        _build_graphrag_payload(workspace, nodes, edges, FakeEmbeddingProvider())
    )

    assert payload["chunks"][0]["scopeKeys"] == ["training", "finance"]
    assert payload["chunks"][0]["embedding"] == [1.0, 0.0]
    assert payload["mentions"][0]["chunkId"] == "chunk-0"
    assert payload["facts"][0]["factType"] == "PROPERTY"
    assert payload["facts"][0]["evidenceChunkIds"] == ["chunk-0"]


def test_neo4j_store_search_methods_parameter_safety() -> None:
    captured_params = {}

    class FakeResult:
        async def data(self):
            return [{"mock": True}]

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def run(self, cypher, **parameters):
            captured_params.clear()
            captured_params.update(parameters)
            return FakeResult()

    class FakeDriver:
        def session(self, database):
            return FakeSession()

    store = Neo4jIngestionStore(
        FakeDriver(), "neo4j", embedding_provider=FakeEmbeddingProvider()
    )
    res_fulltext = asyncio.run(store.search_chunk_fulltext("taekwondo", 5))
    assert res_fulltext == [{"mock": True}]
    assert captured_params.get("query") == "taekwondo"
    assert captured_params.get("limit") == 5

    res_entity = asyncio.run(store.search_entity_fulltext("taekwondo", 5))
    assert res_entity == [{"mock": True}]
    assert captured_params.get("query") == "taekwondo"
    assert captured_params.get("limit") == 5
