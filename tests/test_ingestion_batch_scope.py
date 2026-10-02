import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.schemas.ingestion_schema import (
    IngestionJobStatus,
    OntologyProjection,
    PreparedChunk,
)
from app.services.ingestion.operations import submit_batch


def _projection() -> OntologyProjection:
    return OntologyProjection.model_validate({
        "versionId": "00000000-0000-0000-0000-000000000010",
        "version": "v1", "digest": "merged-hash",
        "scopeKey": "training+finance", "scopeKeys": ["training", "finance"],
        "entityTypes": [{"technicalName": "course"}],
        "properties": [{"entityType": "course", "technicalName": "name", "dataType": "STRING"}],
        "relationships": [], "aliases": [],
    })


def test_submit_validates_and_persists_multiple_batch_scopes_without_neo4j_staging() -> None:
    ontology_id = uuid4()
    batch = SimpleNamespace(batch_index=0, chunk_indexes=[0], validation_attempts=0,
                            status="PENDING", scope_keys=[])
    workspace = SimpleNamespace(
        job=SimpleNamespace(id=uuid4(), ontology_version_id=ontology_id,
                            status=IngestionJobStatus.BATCHING, error_message=None, error_stage=None),
        document=SimpleNamespace(id=uuid4()),
        version=SimpleNamespace(id=uuid4(), ontology_version_id=ontology_id),
        chunks=(PreparedChunk(chunkId="chunk-0", chunkIndex=0,
                              text="Lớp Taekwondo thiếu nhi", contentHash="hash",
                              tokenCount=4, sourceAnchor="classes.md#0"),),
        batches=(batch,),
    )
    captured = {}

    class Repository:
        async def get_workspace(self, ingestion_id):
            return workspace

        async def store_batch_result(self, ingestion_id, batch_index, bindings,
                                     merged_hash, semantic_fragment, fragment, issues,
                                     *, max_attempts):
            captured.update(bindings=bindings, merged_hash=merged_hash,
                            semantic_fragment=semantic_fragment,
                            fragment=fragment, issues=issues)
            batch.scope_keys = [item["scopeKey"] for item in bindings]
            batch.status = "STAGED" if not issues else "REPAIR_REQUIRED"
            return workspace

    class Cache:
        async def get_many(self, scope_keys, ontology_version_id):
            assert scope_keys == ["training", "finance"]
            assert ontology_version_id == str(ontology_id)
            return _projection()

        async def get(self, scope_key, ontology_version_id):
            projection = _projection().model_copy(deep=True)
            projection.digest = f"{scope_key}-hash"
            projection.scope_key = scope_key
            projection.scope_keys = [scope_key]
            return projection

    result = asyncio.run(submit_batch(
        Repository(), Cache(), "ingestion-id", 0, ["training", "finance"],
        {
            "ontologyVersion": "v1",
            "nodes": [{
                "tempId": "course-1", "className": "course",
                "properties": [{
                    "propertyName": "name", "value": "Taekwondo thiếu nhi",
                    "evidence": [{"source": "classes.md", "chunkIndex": 0,
                                  "text": "Taekwondo thiếu nhi"}],
                }],
            }],
            "edges": [],
            "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "course"}],
        },
    ))
    assert result["scopeKeys"] == ["training", "finance"]
    assert captured["merged_hash"] == "merged-hash"
    assert captured["issues"] == []
    assert [item["scopeKey"] for item in captured["bindings"]] == ["training", "finance"]
