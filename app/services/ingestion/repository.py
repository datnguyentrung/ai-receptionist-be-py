"""PostgreSQL repository for ingestion (Cleaned / In-Memory State)."""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.schemas.ingestion_schema import (
    GraphPatchFragment,
    IngestionJobStatus,
    OntologyProjection,
    PreparedChunk,
    SourceVersionStatus,
)
from app.services.ingestion.preprocessing import CHUNKER_VERSION, PreparedDocument

MAPPER_VERSION = "taekwondo-mapper-v1"


@dataclass
class IngestionDocumentData:
    id: uuid.UUID
    document_key: str
    name: str
    source_type: str = "MANUAL_UPLOAD"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class IngestionDocumentVersionData:
    id: uuid.UUID
    document_id: uuid.UUID
    content_hash: str
    config_signature: str
    ingestion_signature: str
    ontology_version_id: uuid.UUID
    ontology_digest: str
    skill_digest: str
    model_id: str
    chunker_version: str
    mapper_version: str
    compiler_version: str
    status: str = SourceVersionStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    committed_at: datetime | None = None


@dataclass
class IngestionJobData:
    id: uuid.UUID
    document_version_id: uuid.UUID
    ontology_version_id: uuid.UUID
    status: str = IngestionJobStatus.RECEIVED
    stage: str = "received"
    scope_hint: str | None = None
    readiness_fingerprint: str | None = None
    error_stage: str | None = None
    error_message: str | None = None
    summary: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


@dataclass
class IngestionChunkData:
    id: uuid.UUID
    document_version_id: uuid.UUID
    chunk_id: str
    chunk_index: int
    text: str
    content_hash: str
    token_count: int
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    source_anchor: str = ""


@dataclass
class IngestionBatchData:
    id: uuid.UUID
    job_id: uuid.UUID
    batch_index: int
    chunk_indexes: list[int]
    scope_key: str = "core"
    status: str = "PENDING"
    graph_fragment: dict | None = None
    validation_issues: list[dict] = field(default_factory=list)
    validation_attempts: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class Workspace:
    document: IngestionDocumentData
    version: IngestionDocumentVersionData
    job: IngestionJobData
    chunks: tuple[IngestionChunkData, ...]
    batches: tuple[IngestionBatchData, ...]


class IngestionRepository:
    """In-Memory Repository quản lý trạng thái phiên Ingestion.

    Không ghi các bảng trung gian vào PostgreSQL.
    """

    def __init__(self, session_factory: Any = None) -> None:
        self._workspaces: dict[uuid.UUID, Workspace] = {}
        self._by_signature: dict[str, uuid.UUID] = {}

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
            {
                "chunker": CHUNKER_VERSION,
                "batchSize": batch_size,
                "scopeHint": scope_hint,
            }
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

        job_id = self._by_signature.get(ingestion_signature)
        if job_id and job_id in self._workspaces:
            ws = self._workspaces[job_id]
            committed = ws.version.status == SourceVersionStatus.COMMITTED
            return ws, not committed, committed

        doc_id = uuid.uuid4()
        ver_id = uuid.uuid4()
        new_job_id = uuid.uuid4()

        document = IngestionDocumentData(
            id=doc_id, document_key=document_key, name=prepared.filename
        )
        version = IngestionDocumentVersionData(
            id=ver_id,
            document_id=doc_id,
            content_hash=prepared.content_hash,
            config_signature=config_signature,
            ingestion_signature=ingestion_signature,
            ontology_version_id=uuid.UUID(projection.version_id)
            if isinstance(projection.version_id, str) and "-" in projection.version_id
            else uuid.uuid4(),
            ontology_digest=projection.digest,
            skill_digest=skill_digest,
            model_id=model_id,
            chunker_version=CHUNKER_VERSION,
            mapper_version=MAPPER_VERSION,
            compiler_version=compiler_version,
            status=SourceVersionStatus.PENDING,
        )

        job = IngestionJobData(
            id=new_job_id,
            document_version_id=ver_id,
            ontology_version_id=version.ontology_version_id,
            status=IngestionJobStatus.BATCHING,
            stage="batching",
            scope_hint=scope_hint,
        )

        chunks = tuple(
            IngestionChunkData(
                id=uuid.uuid4(),
                document_version_id=ver_id,
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
            for chunk in prepared.chunks
        )

        batches_list = []
        for batch_index, start in enumerate(range(0, len(chunks), batch_size)):
            batch_chunks = chunks[start : start + batch_size]
            batches_list.append(
                IngestionBatchData(
                    id=uuid.uuid4(),
                    job_id=new_job_id,
                    batch_index=batch_index,
                    chunk_indexes=[item.chunk_index for item in batch_chunks],
                    scope_key=scope_hint or "core",
                    status="PENDING",
                )
            )
        batches = tuple(batches_list)

        workspace = Workspace(
            document=document,
            version=version,
            job=job,
            chunks=chunks,
            batches=batches,
        )
        self._workspaces[new_job_id] = workspace
        self._by_signature[ingestion_signature] = new_job_id
        return workspace, False, False

    async def get_workspace(self, ingestion_id: str) -> Workspace | None:
        return self._workspaces.get(_uuid(ingestion_id))

    async def list_workspaces(self, document_id: str | None = None) -> list[Workspace]:
        if not document_id:
            return list(self._workspaces.values())
        doc_uuid = _uuid(document_id)
        return [ws for ws in self._workspaces.values() if ws.document.id == doc_uuid]

    async def store_batch_result(
        self,
        ingestion_id: str,
        batch_index: int,
        scope_key: str,
        fragment: GraphPatchFragment | None,
        issues: list[dict],
    ) -> Workspace:
        job_uuid = _uuid(ingestion_id)
        workspace = self._workspaces.get(job_uuid)
        if not workspace:
            raise KeyError(ingestion_id)

        batch = next(
            (item for item in workspace.batches if item.batch_index == batch_index),
            None,
        )
        if batch is None:
            raise KeyError(f"Unknown batch index: {batch_index}")

        batch.validation_attempts += 1
        batch.scope_key = scope_key
        batch.validation_issues = issues
        if issues:
            batch.status = "REPAIR_REQUIRED"
            batch.graph_fragment = None
        else:
            batch.status = "STAGED"
            batch.graph_fragment = (
                fragment.model_dump(by_alias=True, mode="json") if fragment else None
            )

        workspace.job.status = IngestionJobStatus.BATCHING
        workspace.job.stage = "batching"
        workspace.job.readiness_fingerprint = None
        workspace.job.updated_at = datetime.now(timezone.utc)
        return workspace

    async def mark_ready(self, ingestion_id: str, fingerprint: str) -> Workspace:
        job_uuid = _uuid(ingestion_id)
        workspace = self._workspaces.get(job_uuid)
        if not workspace:
            raise KeyError(ingestion_id)
        workspace.job.status = IngestionJobStatus.READY
        workspace.job.stage = "ready_to_fill"
        workspace.job.readiness_fingerprint = fingerprint
        workspace.job.updated_at = datetime.now(timezone.utc)
        return workspace

    async def mark_writing(self, ingestion_id: str) -> Workspace:
        job_uuid = _uuid(ingestion_id)
        workspace = self._workspaces.get(job_uuid)
        if not workspace:
            raise KeyError(ingestion_id)
        workspace.job.status = IngestionJobStatus.WRITING
        workspace.job.stage = "writing"
        workspace.job.updated_at = datetime.now(timezone.utc)
        return workspace

    async def mark_committed(self, ingestion_id: str, summary: dict) -> Workspace:
        job_uuid = _uuid(ingestion_id)
        workspace = self._workspaces.get(job_uuid)
        if not workspace:
            raise KeyError(ingestion_id)
        now = datetime.now(timezone.utc)
        workspace.job.status = IngestionJobStatus.COMMITTED
        workspace.job.stage = "committed"
        workspace.job.completed_at = now
        workspace.job.summary = summary
        workspace.version.status = SourceVersionStatus.COMMITTED
        workspace.version.committed_at = now
        return workspace

    async def mark_failed(
        self, ingestion_id: str, stage: str, message: str
    ) -> Workspace:
        job_uuid = _uuid(ingestion_id)
        workspace = self._workspaces.get(job_uuid)
        if not workspace:
            raise KeyError(ingestion_id)
        workspace.job.status = IngestionJobStatus.FAILED
        workspace.job.stage = stage
        workspace.job.error_stage = stage
        workspace.job.error_message = message
        workspace.version.status = SourceVersionStatus.FAILED
        return workspace

    async def mark_deleted(self, document_id: str) -> tuple[Workspace, list[Workspace]]:
        doc_uuid = _uuid(document_id)
        all_workspaces = [
            ws for ws in self._workspaces.values() if ws.document.id == doc_uuid
        ]
        if not all_workspaces:
            raise KeyError(document_id)
        for ws in all_workspaces:
            ws.version.status = SourceVersionStatus.DELETED
            ws.job.status = IngestionJobStatus.DELETED
        return all_workspaces[-1], all_workspaces

    async def mark_rolled_back(
        self, document_id: str, target_version_id: str
    ) -> tuple[Workspace, list[Workspace]]:
        doc_uuid = _uuid(document_id)
        target_uuid = _uuid(target_version_id)
        all_workspaces = [
            ws for ws in self._workspaces.values() if ws.document.id == doc_uuid
        ]
        target = next(
            (ws for ws in all_workspaces if ws.version.id == target_uuid), None
        )
        if not target:
            raise KeyError(target_version_id)
        for ws in all_workspaces:
            if ws.version.id != target_uuid:
                ws.version.status = SourceVersionStatus.ROLLED_BACK
                ws.job.status = IngestionJobStatus.ROLLED_BACK
        return target, all_workspaces

    async def get_cached_fragment(
        self,
        document_version_id: uuid.UUID,
        batch_index: int,
        graph_context_digest: str,
    ) -> dict | None:
        return None

    async def put_cached_fragment(
        self,
        document_version_id: uuid.UUID,
        batch_index: int,
        graph_context_digest: str,
        fragment: dict,
    ) -> None:
        pass


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _digest(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def workspace_chunks(
    workspace: Workspace, chunk_indexes: list[int]
) -> list[PreparedChunk]:
    index_set = set(chunk_indexes)
    return [
        PreparedChunk(
            chunk_id=item.chunk_id,
            chunk_index=item.chunk_index,
            text=item.text,
            content_hash=item.content_hash,
            token_count=item.token_count,
            section=item.section,
            page_start=item.page_start,
            page_end=item.page_end,
            source_anchor=item.source_anchor,
        )
        for item in workspace.chunks
        if item.chunk_index in index_set
    ]


def workspace_fingerprint(workspace: Workspace) -> str:
    staged = [
        {
            "batchIndex": b.batch_index,
            "scopeKey": b.scope_key,
            "fragment": b.graph_fragment,
        }
        for b in sorted(workspace.batches, key=lambda x: x.batch_index)
        if b.status == "STAGED"
    ]
    return _digest(
        {
            "versionId": str(workspace.version.id),
            "contentHash": workspace.version.content_hash,
            "staged": staged,
        }
    )


__all__ = [
    "CHUNKER_VERSION",
    "MAPPER_VERSION",
    "IngestionBatchData",
    "IngestionChunkData",
    "IngestionDocumentData",
    "IngestionDocumentVersionData",
    "IngestionJobData",
    "IngestionRepository",
    "Workspace",
    "workspace_chunks",
    "workspace_fingerprint",
]
