import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.schemas.ingestion_schema import (
    IngestionJobStatus,
    OntologyProjection,
    PreparedChunk,
)
from app.services.ingestion.operations import submit_batch


def test_submit_validates_and_persists_the_batch_scope() -> None:
    batch = SimpleNamespace(
        batch_index=0,
        chunk_indexes=[0],
        validation_attempts=0,
        status="PENDING",
        scope_key="core",
    )
    workspace = SimpleNamespace(
        job=SimpleNamespace(
            id=uuid4(),
            status=IngestionJobStatus.BATCHING,
            error_message=None,
            error_stage=None,
        ),
        document=SimpleNamespace(id=uuid4()),
        version=SimpleNamespace(id=uuid4(), ontology_version_id=uuid4()),
        chunks=(
            PreparedChunk(
                chunkId="chunk-0",
                chunkIndex=0,
                text="Lớp Taekwondo thiếu nhi",
                contentHash="hash",
                tokenCount=4,
                sourceAnchor="classes.md#0",
            ),
        ),
        batches=(batch,),
    )

    class Repository:
        async def get_workspace(self, ingestion_id):
            return workspace

        async def store_batch_result(
            self, ingestion_id, batch_index, scope_key, fragment, issues
        ):
            assert scope_key == "training"
            assert not issues
            batch.scope_key = scope_key
            batch.status = "STAGED"
            return workspace

    class Cache:
        requested = None

        async def get(self, scope_key):
            self.requested = scope_key
            return OntologyProjection.model_validate(
                {
                    "versionId": "ontology-id",
                    "version": "v1",
                    "digest": "digest",
                    "scopeKey": scope_key,
                    "entityTypes": [{"technicalName": "course"}],
                    "properties": [
                        {
                            "entityType": "course",
                            "technicalName": "name",
                            "dataType": "STRING",
                        }
                    ],
                    "relationships": [],
                    "aliases": [],
                }
            )

    class GraphStore:
        staged = None

        async def stage_batch(self, ingestion_id, batch_index, fragment):
            self.staged = (ingestion_id, batch_index, fragment)

    cache = Cache()
    store = GraphStore()
    result = asyncio.run(
        submit_batch(
            Repository(),
            store,
            cache,
            "ingestion-id",
            0,
            "training",
            {
                "ontologyVersion": "v1",
                "nodes": [
                    {
                        "tempId": "course-1",
                        "className": "course",
                        "properties": [
                            {
                                "propertyName": "name",
                                "value": "Taekwondo thiếu nhi",
                                "evidence": [
                                    {
                                        "source": "classes.md",
                                        "chunkIndex": 0,
                                        "text": "Taekwondo thiếu nhi",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "edges": [],
                "coverage": [
                    {"chunkIndex": 0, "decision": "MAPPED", "reason": "course"}
                ],
            },
        )
    )
    assert cache.requested == "training"
    assert result["scopeKey"] == "training"
    assert batch.scope_key == "training"
    assert store.staged is not None
