import asyncio
import json
from dataclasses import replace
from uuid import UUID, uuid4

from app.schemas.ingestion.document import DocumentChunk, SourceBlock
from app.schemas.ingestion.graph_patch import GraphPatchFragment
from app.schemas.ingestion.ontology import ActiveOntology
from app.services.ingestion.engine.repository import (
    IngestionRepository,
    _workspace_from_payload,
    _workspace_payload,
)


def _chunk() -> DocumentChunk:
    return DocumentChunk(
        index=0, source="same.md", content="same content", chunk_id="chunk-1",
        content_hash="a" * 64, structural_path="root#0", start_line=1, end_line=1,
        blocks=[SourceBlock(blockId="blk_001", type="paragraph", text="same content")],
    )


def test_identical_content_with_different_document_keys_never_collides() -> None:
    async def run():
        repo = IngestionRepository()
        ontology = ActiveOntology(version_id="00000000-0000-0000-0000-000000000001", version="v1.0.0")
        common = dict(
            artifact_name="same.md", content_hash="b" * 64, chunks=[_chunk()], ontology=ontology,
            scope_hint=None, skill_digest="skill", model_id="model", compiler_version="compiler",
            batch_size=1, max_batch_chars=100,
        )
        first, _, _ = await repo.create_or_resume(document_key="docs/first.md", **common)
        second, _, _ = await repo.create_or_resume(document_key="docs/second.md", **common)
        assert first.job.id != second.job.id
        payload = _workspace_payload(first)
        json.dumps(payload)  # Must be JSON-serializable for Postgres JSONB checkpoint
        restored = _workspace_from_payload(payload)
        assert restored.document.document_key == "docs/first.md"
        assert restored.chunks[0].text == "same content"
        assert len(restored.chunks[0].blocks) == 1
        assert restored.chunks[0].blocks[0].block_id == "blk_001"
    asyncio.run(run())


def test_aborted_draft_can_be_started_again_with_same_document() -> None:
    async def run():
        repo = IngestionRepository()
        ontology = ActiveOntology(version_id="00000000-0000-0000-0000-000000000001", version="v1.0.0")
        request = dict(
            artifact_name="same.md", content_hash="b" * 64, chunks=[_chunk()], ontology=ontology,
            document_key="docs/same.md", scope_hint=None, skill_digest="skill", model_id="model",
            compiler_version="compiler", batch_size=1, max_batch_chars=100,
        )
        first, _, _ = await repo.create_or_resume(**request)
        await repo.abort(str(first.job.id))
        second, resumed, committed = await repo.create_or_resume(**request)
        assert second.job.id != first.job.id
        assert not resumed and not committed

    asyncio.run(run())


def test_preview_refreshes_previous_source_version() -> None:
    async def run():
        repo = IngestionRepository()
        ontology = ActiveOntology(version_id="00000000-0000-0000-0000-000000000001", version="v1.0.0")
        base = dict(
            artifact_name="same.md", chunks=[_chunk()], ontology=ontology,
            document_key="docs/same.md", scope_hint=None, skill_digest="skill", model_id="model",
            compiler_version="compiler", batch_size=1, max_batch_chars=100,
        )
        first, _, _ = await repo.create_or_resume(content_hash="a" * 64, **base)
        second, _, _ = await repo.create_or_resume(content_hash="b" * 64, **base)
        assert second.document.current_version_id is None
        await repo.mark_committed(str(first.job.id), {})
        refreshed = await repo.refresh_document_current(str(second.job.id))
        assert refreshed.document.current_version_id == first.version.id

    asyncio.run(run())


def test_same_document_content_remains_idempotent_after_ontology_changes() -> None:
    async def run():
        repo = IngestionRepository()
        request = dict(
            artifact_name="same.md", content_hash="b" * 64, chunks=[_chunk()],
            document_key="docs/same.md", scope_hint=None, skill_digest="skill", model_id="model",
            compiler_version="compiler", batch_size=1, max_batch_chars=100,
        )
        first, _, _ = await repo.create_or_resume(
            ontology=ActiveOntology(version_id="00000000-0000-0000-0000-000000000001", version="v1"),
            **request,
        )
        await repo.mark_committed(str(first.job.id), {})
        again, resumed, committed = await repo.create_or_resume(
            ontology=ActiveOntology(version_id="00000000-0000-0000-0000-000000000002", version="v2"),
            **request,
        )
        assert again.job.id == first.job.id
        assert not resumed and committed

    asyncio.run(run())


def test_rebase_resets_stale_validated_baseline() -> None:
    async def run():
        repo = IngestionRepository()
        old_version_id = "00000000-0000-0000-0000-000000000001"
        target_version_id = uuid4()
        workspace, _, _ = await repo.create_or_resume(
            artifact_name="same.md",
            content_hash="b" * 64,
            chunks=[_chunk()],
            ontology=ActiveOntology(version_id=old_version_id, version="v1.0.0"),
            document_key="docs/same.md",
            scope_hint=None,
            skill_digest="skill",
            model_id="model",
            compiler_version="compiler",
            batch_size=1,
            max_batch_chars=100,
        )
        stale_baseline = GraphPatchFragment(
            ontology_version=old_version_id,
            nodes=[],
            edges=[],
            coverage=[],
            warnings=[],
        )
        stale_batch = replace(
            workspace.batches[0],
            status="REPAIR_REQUIRED",
            validation_attempts=2,
            validation_issues=[
                {
                    "code": "UNKNOWN_ENTITY_TYPE",
                    "message": "Unknown entity type: requirement",
                    "location": "nodes.0.className",
                    "retryable": False,
                }
            ],
            validated_baseline=stale_baseline.model_dump(
                by_alias=True, mode="json"
            ),
            scope_keys=["rank"],
            snapshot_hashes={"rank": "stale-hash"},
        )
        stale_workspace = replace(workspace, batches=(stale_batch,))
        repo._workspaces[str(workspace.job.id)] = stale_workspace

        rebased = await repo.rebase_ontology_version(
            str(workspace.job.id),
            str(target_version_id),
            {"rank": "new-hash"},
            {},
        )

        batch = rebased.batches[0]
        assert batch.status == "PENDING"
        assert batch.validation_attempts == 0
        assert batch.validation_issues == []
        assert batch.validated_baseline is None
        assert batch.semantic_fragment is None
        assert batch.graph_fragment is None
        assert batch.scope_keys == []
        assert batch.snapshot_hashes == {}
        assert rebased.job.ontology_version_id == UUID(str(target_version_id))

    asyncio.run(run())
