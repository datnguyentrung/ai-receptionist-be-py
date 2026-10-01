"""PostgreSQL repository for durable ingestion checkpoints."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.ingestion import (
    IngestionBatch,
    IngestionChunk,
    IngestionDocument,
    IngestionDocumentVersion,
    IngestionJob,
)
from app.schemas.ingestion_schema import (
    GraphPatchFragment,
    IngestionJobStatus,
    OntologyProjection,
    PreparedChunk,
    SourceVersionStatus,
)
from app.services.ingestion.preprocessing import CHUNKER_VERSION, PreparedDocument

MAPPER_VERSION = "taekwondo-mapper-v1"


@dataclass(frozen=True)
class Workspace:
    document: IngestionDocument
    version: IngestionDocumentVersion
    job: IngestionJob
    chunks: tuple[IngestionChunk, ...]
    batches: tuple[IngestionBatch, ...]


class IngestionRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_or_resume(
        self,
        prepared: PreparedDocument,
        projection: OntologyProjection,
        *,
        document_key: str,
        scope_hint: str | None,
        skill_digest: str,
        model_id: str,
        compiler_version: str,
        batch_size: int,
    ) -> tuple[Workspace, bool, bool]:
        config_signature = _digest(
            {"chunker": CHUNKER_VERSION, "batchSize": batch_size, "scopeHint": scope_hint}
        )
        ingestion_signature = _digest(
            {
                "contentHash": prepared.content_hash,
                "configSignature": config_signature,
                "ontologyDigest": projection.digest,
                "skillDigest": skill_digest,
                "modelId": model_id,
                "mapperVersion": MAPPER_VERSION,
                "compilerVersion": compiler_version,
            }
        )
        async with self._session_factory() as session, session.begin():
            document = await session.scalar(
                select(IngestionDocument).where(IngestionDocument.document_key == document_key)
            )
            if document is None:
                document = IngestionDocument(document_key=document_key, name=prepared.filename)
                session.add(document)
                await session.flush()

            version = await session.scalar(
                select(IngestionDocumentVersion).where(
                    IngestionDocumentVersion.document_id == document.id,
                    IngestionDocumentVersion.ingestion_signature == ingestion_signature,
                )
            )
            if version is not None:
                job = await session.scalar(
                    select(IngestionJob)
                    .where(IngestionJob.document_version_id == version.id)
                    .order_by(IngestionJob.created_at.desc())
                )
                if job is None:
                    raise RuntimeError("Document version exists without an ingestion job")
                workspace = await self._workspace_in_session(session, job.id)
                return workspace, version.status != SourceVersionStatus.COMMITTED, version.status == SourceVersionStatus.COMMITTED

            version = IngestionDocumentVersion(
                document_id=document.id,
                content_hash=prepared.content_hash,
                config_signature=config_signature,
                ingestion_signature=ingestion_signature,
                ontology_version_id=uuid.UUID(projection.version_id),
                ontology_digest=projection.digest,
                skill_digest=skill_digest,
                model_id=model_id,
                chunker_version=CHUNKER_VERSION,
                mapper_version=MAPPER_VERSION,
                compiler_version=compiler_version,
                status=SourceVersionStatus.PENDING,
            )
            session.add(version)
            await session.flush()
            job = IngestionJob(
                document_version_id=version.id,
                ontology_version_id=version.ontology_version_id,
                status=IngestionJobStatus.BATCHING,
                stage="batching",
                scope_hint=scope_hint,
                summary={"warnings": list(prepared.warnings)},
            )
            session.add(job)
            await session.flush()
            for chunk in prepared.chunks:
                session.add(
                    IngestionChunk(
                        document_version_id=version.id,
                        chunk_id=chunk.chunk_id,
                        chunk_index=chunk.chunk_index,
                        text=chunk.text,
                        content_hash=chunk.content_hash,
                        token_count=chunk.token_count,
                        section=chunk.section,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        source_anchor=chunk.source_anchor,
                    )
                )
            for batch_index, start in enumerate(range(0, len(prepared.chunks), batch_size)):
                session.add(
                    IngestionBatch(
                        job_id=job.id,
                        batch_index=batch_index,
                        chunk_indexes=[
                            item.chunk_index for item in prepared.chunks[start : start + batch_size]
                        ],
                    )
                )
            await session.flush()
            return await self._workspace_in_session(session, job.id), False, False

    async def get_workspace(self, ingestion_id: str | uuid.UUID) -> Workspace | None:
        job_id = _uuid(ingestion_id)
        async with self._session_factory() as session:
            if await session.get(IngestionJob, job_id) is None:
                return None
            return await self._workspace_in_session(session, job_id)

    async def store_batch_result(
        self,
        ingestion_id: str,
        batch_index: int,
        fragment: GraphPatchFragment | None,
        issues: list[dict],
    ) -> Workspace:
        async with self._session_factory() as session, session.begin():
            batch = await session.scalar(
                select(IngestionBatch).where(
                    IngestionBatch.job_id == _uuid(ingestion_id),
                    IngestionBatch.batch_index == batch_index,
                )
            )
            if batch is None:
                raise KeyError(f"Unknown batch index: {batch_index}")
            batch.validation_attempts += 1
            batch.validation_issues = issues
            if issues:
                batch.status = "REPAIR_REQUIRED"
                batch.graph_fragment = None
            else:
                batch.status = "STAGED"
                batch.graph_fragment = fragment.model_dump(by_alias=True, mode="json") if fragment else None
            job = await session.get(IngestionJob, batch.job_id)
            if job is None:
                raise RuntimeError("Ingestion batch has no job")
            job.status = IngestionJobStatus.BATCHING
            job.stage = "batching"
            job.readiness_fingerprint = None
            await session.flush()
            return await self._workspace_in_session(session, job.id)

    async def mark_ready(self, ingestion_id: str, fingerprint: str) -> Workspace:
        async with self._session_factory() as session, session.begin():
            job = await session.get(IngestionJob, _uuid(ingestion_id))
            if job is None:
                raise KeyError(ingestion_id)
            job.status = IngestionJobStatus.READY
            job.stage = "ready_to_fill"
            job.readiness_fingerprint = fingerprint
            await session.flush()
            return await self._workspace_in_session(session, job.id)

    async def mark_writing(self, ingestion_id: str) -> Workspace:
        return await self._set_job(ingestion_id, IngestionJobStatus.WRITING, "writing")

    async def mark_committed(self, ingestion_id: str, summary: dict) -> Workspace:
        async with self._session_factory() as session, session.begin():
            workspace = await self._workspace_in_session(session, _uuid(ingestion_id))
            now = datetime.now(timezone.utc)
            workspace.job.status = IngestionJobStatus.COMMITTED
            workspace.job.stage = "committed"
            workspace.job.summary = {**(workspace.job.summary or {}), **summary}
            workspace.job.completed_at = now
            workspace.version.status = SourceVersionStatus.COMMITTED
            workspace.version.committed_at = now
            previous_version_id = workspace.document.current_version_id
            workspace.document.current_version_id = workspace.version.id
            if previous_version_id and previous_version_id != workspace.version.id:
                previous = await session.get(IngestionDocumentVersion, previous_version_id)
                if previous and previous.status == SourceVersionStatus.COMMITTED:
                    previous.status = SourceVersionStatus.SUPERSEDED
            await session.flush()
            return await self._workspace_in_session(session, workspace.job.id)

    async def mark_failed(self, ingestion_id: str, stage: str, message: str) -> Workspace:
        async with self._session_factory() as session, session.begin():
            workspace = await self._workspace_in_session(session, _uuid(ingestion_id))
            workspace.job.status = IngestionJobStatus.FAILED
            workspace.job.stage = "failed"
            workspace.job.error_stage = stage
            workspace.job.error_message = message[:4000]
            workspace.version.status = SourceVersionStatus.FAILED
            await session.flush()
            return await self._workspace_in_session(session, workspace.job.id)

    async def set_document_version_status(
        self, document_id: str, version_id: str | None, status: SourceVersionStatus
    ) -> tuple[IngestionDocument, IngestionDocumentVersion]:
        async with self._session_factory() as session, session.begin():
            document = await session.get(IngestionDocument, _uuid(document_id))
            if document is None:
                raise KeyError(document_id)
            target_id = _uuid(version_id) if version_id else document.current_version_id
            if target_id is None:
                raise KeyError("Document has no current version")
            version = await session.get(IngestionDocumentVersion, target_id)
            if version is None or version.document_id != document.id:
                raise KeyError(str(target_id))
            version.status = status
            if document.current_version_id == version.id:
                previous = await session.scalar(
                    select(IngestionDocumentVersion)
                    .where(
                        IngestionDocumentVersion.document_id == document.id,
                        IngestionDocumentVersion.id != version.id,
                        IngestionDocumentVersion.status.in_([
                            SourceVersionStatus.COMMITTED,
                            SourceVersionStatus.SUPERSEDED,
                        ]),
                    )
                    .order_by(IngestionDocumentVersion.committed_at.desc())
                )
                document.current_version_id = previous.id if previous else None
                if previous:
                    previous.status = SourceVersionStatus.COMMITTED
            return document, version

    async def _set_job(self, ingestion_id: str, status: IngestionJobStatus, stage: str) -> Workspace:
        async with self._session_factory() as session, session.begin():
            job = await session.get(IngestionJob, _uuid(ingestion_id))
            if job is None:
                raise KeyError(ingestion_id)
            job.status = status
            job.stage = stage
            await session.flush()
            return await self._workspace_in_session(session, job.id)

    async def _workspace_in_session(self, session: AsyncSession, job_id: uuid.UUID) -> Workspace:
        job = await session.get(IngestionJob, job_id)
        if job is None:
            raise KeyError(str(job_id))
        version = await session.get(IngestionDocumentVersion, job.document_version_id)
        if version is None:
            raise RuntimeError("Ingestion job has no document version")
        document = await session.get(IngestionDocument, version.document_id)
        if document is None:
            raise RuntimeError("Ingestion version has no document")
        chunks = tuple(
            (
                await session.scalars(
                    select(IngestionChunk)
                    .where(IngestionChunk.document_version_id == version.id)
                    .order_by(IngestionChunk.chunk_index)
                )
            ).all()
        )
        batches = tuple(
            (
                await session.scalars(
                    select(IngestionBatch)
                    .where(IngestionBatch.job_id == job.id)
                    .order_by(IngestionBatch.batch_index)
                )
            ).all()
        )
        return Workspace(document=document, version=version, job=job, chunks=chunks, batches=batches)


def workspace_chunks(workspace: Workspace, indexes: list[int]) -> list[PreparedChunk]:
    selected = {item.chunk_index: item for item in workspace.chunks}
    return [
        PreparedChunk(
            chunk_id=selected[index].chunk_id,
            chunk_index=index,
            text=selected[index].text,
            content_hash=selected[index].content_hash,
            token_count=selected[index].token_count,
            section=selected[index].section,
            page_start=selected[index].page_start,
            page_end=selected[index].page_end,
            source_anchor=selected[index].source_anchor,
        )
        for index in indexes
    ]


def workspace_fingerprint(workspace: Workspace) -> str:
    return _digest(
        [
            {"batchIndex": item.batch_index, "fragment": item.graph_fragment}
            for item in workspace.batches
        ]
    )


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _uuid(value: str | uuid.UUID | None) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if value is None:
        raise ValueError("UUID is required")
    return uuid.UUID(str(value))


__all__ = [
    "MAPPER_VERSION",
    "IngestionRepository",
    "Workspace",
    "workspace_chunks",
    "workspace_fingerprint",
]
