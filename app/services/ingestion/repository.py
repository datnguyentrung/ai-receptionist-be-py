from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from app.schemas.ingestion_schema import (
    ActiveOntology,
    GraphPatchFragment,
    IngestionJobStatus,
    PreparedChunk,
    SourceVersionStatus,
)
from app.services.ingestion.preprocessing import CHUNKER_VERSION, PreparedDocument

MAPPER_VERSION = "taekwondo-mapper-v2"


@dataclass(frozen=True)
class IngestionDocumentData:
    id: uuid.UUID
    document_key: str
    name: str
    current_version_id: uuid.UUID | None


@dataclass(frozen=True)
class IngestionDocumentVersionData:
    id: uuid.UUID
    document_id: uuid.UUID
    content_hash: str
    ontology_version_id: uuid.UUID
    ontology_digest: str
    status: str


@dataclass
class IngestionJobData:
    id: uuid.UUID
    document_version_id: uuid.UUID
    ontology_version_id: uuid.UUID
    status: str
    stage: str
    scope_hint: str | None
    readiness_fingerprint: str | None
    error_stage: str | None
    error_message: str | None
    summary: dict = field(default_factory=dict)


@dataclass(frozen=True)
class IngestionChunkData:
    id: uuid.UUID
    document_version_id: uuid.UUID
    chunk_id: str
    chunk_index: int
    text: str
    content_hash: str
    token_count: int
    section: str | None
    page_start: int | None
    page_end: int | None
    source_anchor: str


@dataclass
class IngestionBatchData:
    id: uuid.UUID
    job_id: uuid.UUID
    batch_index: int
    chunk_indexes: list[int]
    status: str
    graph_fragment: dict | None
    validation_issues: list[dict]
    validation_attempts: int
    merged_schema_hash: str | None
    scope_keys: list[str] = field(default_factory=list)
    snapshot_hashes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Workspace:
    document: IngestionDocumentData
    version: IngestionDocumentVersionData
    job: IngestionJobData
    chunks: tuple[IngestionChunkData, ...]
    batches: tuple[IngestionBatchData, ...]


class IngestionRepository:
    """Process-local ingestion workspace store (in-memory RAM).

    PostgreSQL is deliberately not used for ingestion job state. Durable PostgreSQL
    storage is reserved for ontology versions, scopes, compiled snapshots, and schema
    proposals. Knowledge graph data is persisted in Neo4j.
    """

    def __init__(self, *_: Any, **__: Any) -> None:
        self._workspaces: dict[str, Workspace] = {}
        self._by_signature: dict[str, str] = {}
        self._document_current_version: dict[str, uuid.UUID] = {}

    async def create_or_resume(
        self,
        prepared: PreparedDocument,
        ontology: ActiveOntology,
        *,
        document_key: str,
        scope_hint: str | None,
        skill_digest: str,
        model_id: str,
        compiler_version: str,
        batch_size: int,
    ) -> tuple[Workspace, bool, bool]:
        """Tạo mới hoặc tái sử dụng workspace ingestion còn tồn tại trong tiến trình hiện tại (in-process workspace reuse)."""
        config_signature = _digest(
            {"chunker": CHUNKER_VERSION, "batchSize": batch_size}
        )
        ingestion_signature = _digest(
            {
                "contentHash": prepared.content_hash,
                "configSignature": config_signature,
                "ontologyVersionId": ontology.version_id,
                "skillDigest": skill_digest,
                "modelId": model_id,
                "mapperVersion": MAPPER_VERSION,
                "compilerVersion": compiler_version,
            }
        )
        existing_id = self._by_signature.get(ingestion_signature)
        if existing_id:
            workspace = self._workspaces[existing_id]
            committed = workspace.version.status == SourceVersionStatus.COMMITTED
            return workspace, not committed, committed

        document_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"ingestion-document:{document_key}"
        )
        version_id = uuid.uuid4()
        job_id = uuid.uuid4()
        ontology_id = _uuid(ontology.version_id)
        document = IngestionDocumentData(
            id=document_id,
            document_key=document_key,
            name=prepared.filename,
            current_version_id=self._document_current_version.get(document_key),
        )
        version = IngestionDocumentVersionData(
            id=version_id,
            document_id=document_id,
            content_hash=prepared.content_hash,
            ontology_version_id=ontology_id,
            ontology_digest=_digest(
                {"versionId": ontology.version_id, "version": ontology.version}
            ),
            status=SourceVersionStatus.PENDING,
        )
        job = IngestionJobData(
            id=job_id,
            document_version_id=version_id,
            ontology_version_id=ontology_id,
            status=IngestionJobStatus.BATCHING,
            stage="batching",
            scope_hint=scope_hint,
            readiness_fingerprint=None,
            error_stage=None,
            error_message=None,
            summary={},
        )
        chunks = tuple(
            IngestionChunkData(
                id=uuid.uuid4(),
                document_version_id=version_id,
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
        batches = tuple(
            IngestionBatchData(
                id=uuid.uuid4(),
                job_id=job_id,
                batch_index=batch_index,
                chunk_indexes=[
                    item.chunk_index for item in chunks[start : start + batch_size]
                ],
                status="PENDING",
                graph_fragment=None,
                validation_issues=[],
                validation_attempts=0,
                merged_schema_hash=None,
            )
            for batch_index, start in enumerate(range(0, len(chunks), batch_size))
        )
        workspace = Workspace(
            document=document, version=version, job=job, chunks=chunks, batches=batches
        )
        self._workspaces[str(job_id)] = workspace
        self._by_signature[ingestion_signature] = str(job_id)
        return workspace, False, False

    async def get_workspace(self, ingestion_id: str) -> Workspace | None:
        return self._workspaces.get(str(ingestion_id))

    async def list_committed_workspaces(
        self, document_id: str | None = None, limit: int = 1000
    ) -> list[Workspace]:
        workspaces = [
            item
            for item in self._workspaces.values()
            if item.job.status == IngestionJobStatus.COMMITTED
            and (document_id is None or str(item.document.id) == document_id)
        ]
        return workspaces[:limit]

    async def store_batch_result(
        self,
        ingestion_id: str,
        batch_index: int,
        scope_bindings: list[dict[str, str]],
        merged_schema_hash: str | None,
        fragment: GraphPatchFragment | None,
        issues: list[dict],
    ) -> Workspace:
        workspace, batch = self._workspace_and_batch(ingestion_id, batch_index)
        if workspace.job.status != IngestionJobStatus.BATCHING:
            raise RuntimeError(
                f"Cannot submit a batch while ingestion is {workspace.job.status}"
            )
        if batch.status in {"BLOCKED_SCHEMA", "SCHEMA_REJECTED"}:
            raise RuntimeError(
                f"Batch {batch_index} is gated by schema review ({batch.status})"
            )
        updated = replace(
            batch,
            validation_attempts=batch.validation_attempts + 1,
            validation_issues=issues,
            merged_schema_hash=merged_schema_hash,
            graph_fragment=fragment.model_dump(by_alias=True, mode="json")
            if hasattr(fragment, "model_dump")
            else fragment,
            status="REPAIR_REQUIRED" if issues else "STAGED",
            scope_keys=[item["scopeKey"] for item in scope_bindings],
            snapshot_hashes={
                item["scopeKey"]: item["schemaHash"] for item in scope_bindings
            },
        )
        workspace.job.status = IngestionJobStatus.BATCHING
        workspace.job.stage = "batching"
        workspace.job.readiness_fingerprint = None
        return self._replace_batch(workspace, updated)

    async def block_batch_for_proposal(
        self, ingestion_id: str, batch_index: int, issues: list[dict]
    ) -> Workspace:
        workspace, batch = self._workspace_and_batch(ingestion_id, batch_index)
        updated = replace(
            batch,
            status="BLOCKED_SCHEMA",
            validation_issues=issues,
            graph_fragment=None,
        )
        workspace.job.status = "BLOCKED_SCHEMA"
        workspace.job.stage = "awaiting_schema_approval"
        workspace.job.readiness_fingerprint = None
        return self._replace_batch(workspace, updated)

    async def reject_batch_schema_proposal(
        self, ingestion_id: str, batch_index: int, issues: list[dict]
    ) -> Workspace:
        workspace, batch = self._workspace_and_batch(ingestion_id, batch_index)
        updated = replace(
            batch,
            status="SCHEMA_REJECTED",
            validation_issues=issues,
            graph_fragment=None,
        )
        workspace.job.status = IngestionJobStatus.BATCHING
        workspace.job.stage = "schema_proposal_rejected"
        workspace.job.readiness_fingerprint = None
        return self._replace_batch(workspace, updated)

    async def mark_ready(self, ingestion_id: str, fingerprint: str) -> Workspace:
        workspace = self._required(ingestion_id)
        if not workspace.batches or any(
            item.status != "STAGED" for item in workspace.batches
        ):
            raise RuntimeError("Every ingestion batch must be STAGED before finalize")
        workspace.job.status = IngestionJobStatus.READY
        workspace.job.stage = "ready_to_fill"
        workspace.job.readiness_fingerprint = fingerprint
        return workspace

    async def mark_writing(self, ingestion_id: str) -> Workspace:
        workspace = self._required(ingestion_id)
        if (
            workspace.job.status != IngestionJobStatus.READY
            or not workspace.job.readiness_fingerprint
        ):
            raise RuntimeError("Ingestion must be finalized before writing")
        workspace.job.status = IngestionJobStatus.WRITING
        workspace.job.stage = "writing"
        return workspace

    async def mark_committed(self, ingestion_id: str, summary: dict) -> Workspace:
        """Đánh dấu trạng thái COMMITTED cho workspace trong bộ nhớ RAM."""
        workspace = self._required(ingestion_id)
        now = datetime.now(timezone.utc).isoformat()
        workspace.job.status = IngestionJobStatus.COMMITTED
        workspace.job.stage = "committed"
        workspace.job.summary = {**summary, "committedAt": now}
        workspace = self._replace_version_status(
            workspace, SourceVersionStatus.COMMITTED
        )
        workspace = replace(
            workspace,
            document=replace(
                workspace.document, current_version_id=workspace.version.id
            ),
        )
        self._document_current_version[workspace.document.document_key] = (
            workspace.version.id
        )
        self._workspaces[str(workspace.job.id)] = workspace
        return workspace

    async def mark_failed(
        self, ingestion_id: str, stage: str, message: str
    ) -> Workspace:
        workspace = self._required(ingestion_id)
        workspace.job.status = IngestionJobStatus.FAILED
        workspace.job.stage = stage
        workspace.job.error_stage = stage
        workspace.job.error_message = message
        workspace = self._replace_version_status(workspace, SourceVersionStatus.FAILED)
        self._workspaces[str(workspace.job.id)] = workspace
        return workspace

    async def set_document_version_status(
        self, document_id: str, version_id: str | None, status: SourceVersionStatus
    ) -> tuple[IngestionDocumentData, IngestionDocumentVersionData]:
        target = next(
            (
                item
                for item in self._workspaces.values()
                if str(item.document.id) == document_id
                and (version_id is None or str(item.version.id) == version_id)
            ),
            None,
        )
        if target is None:
            raise KeyError(version_id or document_id)
        updated = self._replace_version_status(target, status)
        if status in {SourceVersionStatus.DELETED, SourceVersionStatus.ROLLED_BACK}:
            updated = replace(
                updated, document=replace(updated.document, current_version_id=None)
            )
            self._document_current_version.pop(updated.document.document_key, None)
        self._workspaces[str(updated.job.id)] = updated
        return updated.document, updated.version

    async def rebase_ontology_version(
        self,
        ingestion_id: str,
        target_version_id: str,
        valid_snapshot_hashes: dict[str, str],
        merged_schema_hashes: dict[str, str],
    ) -> Workspace:
        workspace = self._required(ingestion_id)
        target_uuid = _uuid(target_version_id)
        batches: list[IngestionBatchData] = []
        for batch in workspace.batches:
            unchanged = bool(batch.scope_keys) and all(
                valid_snapshot_hashes.get(scope_key)
                == batch.snapshot_hashes.get(scope_key)
                for scope_key in batch.scope_keys
            )
            if unchanged and batch.graph_fragment:
                graph_fragment = {
                    **batch.graph_fragment,
                    "ontologyVersion": target_version_id,
                }
                batches.append(
                    replace(
                        batch,
                        graph_fragment=graph_fragment,
                        validation_issues=[],
                        merged_schema_hash=merged_schema_hashes.get(
                            "\x1f".join(batch.scope_keys),
                            batch.merged_schema_hash,
                        ),
                    )
                )
            else:
                batches.append(
                    replace(
                        batch,
                        status="PENDING",
                        graph_fragment=None,
                        validation_issues=[],
                        merged_schema_hash=None,
                        scope_keys=[],
                        snapshot_hashes={},
                    )
                )
        workspace.job.ontology_version_id = target_uuid
        workspace.job.status = IngestionJobStatus.BATCHING
        workspace.job.stage = "batching"
        workspace.job.readiness_fingerprint = None
        workspace = replace(
            workspace,
            version=replace(
                workspace.version,
                ontology_version_id=target_uuid,
                ontology_digest=_digest({"versionId": target_version_id}),
            ),
            batches=tuple(batches),
        )
        self._workspaces[str(workspace.job.id)] = workspace
        return workspace

    def _required(self, ingestion_id: str) -> Workspace:
        workspace = self._workspaces.get(str(ingestion_id))
        if workspace is None:
            raise KeyError(ingestion_id)
        return workspace

    def _workspace_and_batch(
        self, ingestion_id: str, batch_index: int
    ) -> tuple[Workspace, IngestionBatchData]:
        workspace = self._required(ingestion_id)
        batch = next(
            (item for item in workspace.batches if item.batch_index == batch_index),
            None,
        )
        if batch is None:
            raise KeyError(f"Unknown batch index: {batch_index}")
        return workspace, batch

    def _replace_batch(
        self, workspace: Workspace, batch: IngestionBatchData
    ) -> Workspace:
        updated = replace(
            workspace,
            batches=tuple(
                batch if item.batch_index == batch.batch_index else item
                for item in workspace.batches
            ),
        )
        self._workspaces[str(updated.job.id)] = updated
        return updated

    @staticmethod
    def _replace_version_status(
        workspace: Workspace, status: SourceVersionStatus
    ) -> Workspace:
        return replace(workspace, version=replace(workspace.version, status=status))


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _digest(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def workspace_chunks(
    workspace: Workspace, chunk_indexes: list[int]
) -> list[PreparedChunk]:
    indexes = set(chunk_indexes)
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
        if item.chunk_index in indexes
    ]


def workspace_fingerprint(workspace: Workspace) -> str:
    return _digest(
        {
            "versionId": str(workspace.version.id),
            "ontologyVersionId": str(workspace.job.ontology_version_id),
            "contentHash": workspace.version.content_hash,
            "staged": [
                {
                    "batchIndex": item.batch_index,
                    "scopeKeys": item.scope_keys,
                    "mergedSchemaHash": item.merged_schema_hash,
                    "fragment": item.graph_fragment,
                }
                for item in workspace.batches
                if item.status == "STAGED"
            ],
        }
    )


def snapshot_bindings_unchanged(
    bindings: list[Any], target_hashes: dict[str, str]
) -> bool:
    return bool(bindings) and all(
        target_hashes.get(item.scope_key) == item.snapshot_hash for item in bindings
    )


__all__ = [
    "MAPPER_VERSION",
    "IngestionBatchData",
    "IngestionChunkData",
    "IngestionDocumentData",
    "IngestionDocumentVersionData",
    "IngestionJobData",
    "IngestionRepository",
    "Workspace",
    "snapshot_bindings_unchanged",
    "workspace_chunks",
    "workspace_fingerprint",
]
