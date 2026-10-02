import asyncio
from types import SimpleNamespace

from app.services.ingestion.graph_store import _build_graphrag_payload, _merge_fragments


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
        version=SimpleNamespace(id="00000000-0000-0000-0000-000000000001", ontology_version_id="00000000-0000-0000-0000-000000000002"),
        document=SimpleNamespace(id="00000000-0000-0000-0000-000000000003", name="course.md"),
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
        batches=(SimpleNamespace(scope_key="training", chunk_indexes=[0]),),
    )

    payload = asyncio.run(
        _build_graphrag_payload(workspace, nodes, edges, FakeEmbeddingProvider())
    )

    assert payload["chunks"][0]["scopeKey"] == "training"
    assert payload["chunks"][0]["embedding"] == [1.0, 0.0]
    assert payload["mentions"][0]["chunkId"] == "chunk-0"
    assert payload["facts"][0]["factType"] == "PROPERTY"
    assert payload["facts"][0]["evidenceChunkIds"] == ["chunk-0"]
