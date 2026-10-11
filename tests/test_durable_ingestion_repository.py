import asyncio

from app.schemas.ingestion.document import DocumentChunk
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
        restored = _workspace_from_payload(_workspace_payload(first))
        assert restored.document.document_key == "docs/first.md"
        assert restored.chunks[0].text == "same content"
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
