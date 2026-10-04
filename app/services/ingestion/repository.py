import hashlib
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from app.core.schemas.ingestion import DocumentChunk
from app.schemas.ingestion_schema import (
    ActiveOntology,
    GraphPatchFragment,
    IngestionJobStatus,
    PreparedChunk,
    SemanticGraphPatchFragment,
    SourceVersionStatus,
)
from app.services.ingestion.document.strategies import STRUCTURAL_CHUNKER_VERSION
from app.services.ingestion.workspace.staged_ingestion import (
    IngestionWorkspaceService,
)

MAPPER_VERSION = "taekwondo-mapper-v2"
_UNSET = object()


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
    semantic_fragment: dict | None
    graph_fragment: dict | None
    validation_issues: list[dict]
    validation_attempts: int = 0
    merged_schema_hash: str | None = None
    scope_keys: list[str] = field(default_factory=list)
    snapshot_hashes: dict[str, str] = field(default_factory=dict)
    validated_baseline: dict | None = None
    claim_ledger: dict | None = None
    schema_gaps: list[dict] = field(default_factory=list)



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
        *,
        artifact_name: str,
        content_hash: str,
        chunks: list[DocumentChunk],
        ontology: ActiveOntology,
        document_key: str,
        scope_hint: str | None,
        skill_digest: str,
        model_id: str,
        compiler_version: str,
        batch_size: int,
        max_batch_chars: int,
    ) -> tuple[Workspace, bool, bool]:
        """Tạo mới hoặc tái sử dụng workspace ingestion còn tồn tại trong tiến trình hiện tại (in-process workspace reuse)."""
        config_signature = _digest(
            {
                "chunker": STRUCTURAL_CHUNKER_VERSION,
                "maxBatchChunks": batch_size,
                "maxBatchChars": max_batch_chars,
            }
        )
        ingestion_signature = _digest(
            {
                "contentHash": content_hash,
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
            if (
                workspace.job.status != IngestionJobStatus.FAILED
                and workspace.version.status != SourceVersionStatus.FAILED
            ):
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
            name=artifact_name,
            current_version_id=self._document_current_version.get(document_key),
        )
        version = IngestionDocumentVersionData(
            id=version_id,
            document_id=document_id,
            content_hash=content_hash,
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
        ingestion_chunks = tuple(
            IngestionChunkData(
                id=uuid.uuid4(),
                document_version_id=version_id,
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.index,
                text=chunk.content,
                content_hash=chunk.content_hash,
                token_count=max(1, (len(chunk.content) + 3) // 4),
                section=chunk.section,
                page_start=None,
                page_end=None,
                source_anchor=f"{chunk.source}#{chunk.structural_path}:L{chunk.start_line}-L{chunk.end_line}",
            )
            for chunk in chunks
        )
        partitioned = IngestionWorkspaceService.partition(
            chunks,
            max_batch_chunks=batch_size,
            max_batch_chars=max_batch_chars,
        )
        batches = tuple(
            IngestionBatchData(
                id=uuid.uuid4(),
                job_id=job_id,
                batch_index=batch.index,
                chunk_indexes=batch.chunk_indexes,
                status="PENDING",
                semantic_fragment=None,
                graph_fragment=None,
                validation_issues=[],
                validation_attempts=0,
                merged_schema_hash=None,
            )
            for batch in partitioned
        )
        workspace = Workspace(
            document=document,
            version=version,
            job=job,
            chunks=ingestion_chunks,
            batches=batches,
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
        semantic_fragment: SemanticGraphPatchFragment | dict | None,
        fragment: GraphPatchFragment | dict | None,
        issues: list[dict],
        *,
        max_attempts: int,
        validated_baseline: GraphPatchFragment | dict | None = None,
        count_attempt: bool = True,
        claim_ledger: dict | None = None,
        schema_gaps: list[dict] | None = None,
        status: str | None = None,
    ) -> Workspace:
        workspace, batch = self._workspace_and_batch(ingestion_id, batch_index)
        if workspace.job.status not in {IngestionJobStatus.BATCHING, "BLOCKED_SCHEMA"}:
            raise RuntimeError(
                f"Cannot submit a batch while ingestion is {workspace.job.status}"
            )
        if batch.status in {"BLOCKED_SCHEMA", "SCHEMA_REJECTED"} and status != "STAGED":
            raise RuntimeError(
                f"Batch {batch_index} is gated by schema review ({batch.status})"
            )
        attempts = batch.validation_attempts + (1 if count_attempt else 0)
        terminal = bool(issues) and count_attempt and attempts > max_attempts

        if not issues and fragment is not None:
            baseline_dict = (
                fragment.model_dump(by_alias=True, mode="json")
                if hasattr(fragment, "model_dump")
                else fragment
            )
        elif validated_baseline is not None:
            baseline_dict = (
                validated_baseline.model_dump(by_alias=True, mode="json")
                if hasattr(validated_baseline, "model_dump")
                else validated_baseline
            )
        else:
            baseline_dict = batch.validated_baseline

        batch_status = status or ("FAILED" if terminal else "REPAIR_REQUIRED" if issues else "STAGED")

        updated = replace(
            batch,
            validation_attempts=attempts,
            validation_issues=issues,
            merged_schema_hash=merged_schema_hash,
            semantic_fragment=semantic_fragment.model_dump(by_alias=True, mode="json")
            if hasattr(semantic_fragment, "model_dump")
            else semantic_fragment,
            graph_fragment=fragment.model_dump(by_alias=True, mode="json")
            if hasattr(fragment, "model_dump")
            else fragment,
            validated_baseline=baseline_dict,
            status=batch_status,
            scope_keys=[item["scopeKey"] for item in scope_bindings],
            snapshot_hashes={
                item["scopeKey"]: item["schemaHash"] for item in scope_bindings
            },
            claim_ledger=claim_ledger if claim_ledger is not None else batch.claim_ledger,
            schema_gaps=schema_gaps if schema_gaps is not None else batch.schema_gaps,
        )
        workspace.job.status = (
            IngestionJobStatus.FAILED if (terminal or batch_status == "EXTRACTION_REJECTED") else IngestionJobStatus.BATCHING
        )
        workspace.job.stage = "explicit_extraction_failure" if terminal else "batching"
        if terminal:
            workspace.job.error_stage = "explicit_extraction_failure"
            workspace.job.error_message = (
                f"Batch {batch_index} exceeded the validation retry limit"
            )
            workspace = self._replace_version_status(
                workspace, SourceVersionStatus.FAILED
            )
        workspace.job.readiness_fingerprint = None
        return self._replace_batch(workspace, updated)

    async def mark_batch_for_repair(
        self,
        ingestion_id: str,
        batch_index: int,
        issues: list[dict],
        *,
        validated_baseline: GraphPatchFragment | dict | None | object = _UNSET,
    ) -> Workspace:
        """Reopen a staged batch when a deterministic finalize-time invariant fails."""
        workspace, batch = self._workspace_and_batch(ingestion_id, batch_index)
        baseline = batch.validated_baseline
        if validated_baseline is not _UNSET:
            baseline = (
                validated_baseline.model_dump(by_alias=True, mode="json")
                if hasattr(validated_baseline, "model_dump")
                else validated_baseline
            )
        updated = replace(
            batch,
            status="REPAIR_REQUIRED",
            validation_issues=issues,
            validated_baseline=baseline,
        )
        workspace.job.status = IngestionJobStatus.BATCHING
        workspace.job.stage = "batching"
        workspace.job.readiness_fingerprint = None
        return self._replace_batch(workspace, updated)

    async def block_batch_for_proposal(
        self,
        ingestion_id: str,
        batch_index: int,
        issues: list[dict],
        *,
        claim_ledger: dict | None = None,
        schema_gaps: list[dict] | None = None,
    ) -> Workspace:
        workspace, batch = self._workspace_and_batch(ingestion_id, batch_index)
        updated = replace(
            batch,
            status="BLOCKED_SCHEMA",
            validation_issues=issues,
            graph_fragment=None,
            claim_ledger=claim_ledger if claim_ledger is not None else batch.claim_ledger,
            schema_gaps=schema_gaps if schema_gaps is not None else batch.schema_gaps,
        )
        workspace.job.status = "BLOCKED_SCHEMA"
        workspace.job.stage = "schema_review_required"
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
                        semantic_fragment={
                            **batch.semantic_fragment,
                            "ontologyVersion": target_version_id,
                        }
                        if batch.semantic_fragment
                        else None,
                        validated_baseline={
                            **batch.validated_baseline,
                            "ontologyVersion": target_version_id,
                        }
                        if batch.validated_baseline
                        else None,
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
                        validation_attempts=0,
                        graph_fragment=None,
                        semantic_fragment=None,
                        validated_baseline=None,
                        validation_issues=[],
                        merged_schema_hash=None,
                        scope_keys=list(batch.scope_keys),
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


def workspace_fingerprint(
    workspace: Workspace, *, merged_graph_digest: str | None = None
) -> str:
    return _digest(
        {
            "versionId": str(workspace.version.id),
            "ontologyVersionId": str(workspace.job.ontology_version_id),
            "ontologyDigest": getattr(workspace.version, "ontology_digest", None),
            "contentHash": workspace.version.content_hash,
            "mergedGraphDigest": merged_graph_digest,
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


def stable_entity_key(class_name: str, identity: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {"class": class_name, "identity": identity},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def staged_entity_index(
    workspace: Workspace, before_batch: int | None = None
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for batch in workspace.batches:
        if before_batch is not None and batch.batch_index >= before_batch:
            continue
        if batch.status != "STAGED" or not batch.graph_fragment:
            continue
        for node in batch.graph_fragment.get("nodes", []):
            identity = node.get("identity") or {}
            key = stable_entity_key(node["className"], identity)
            display_properties = {
                prop.get("propertyName"): prop.get("value")
                for prop in node.get("properties", [])
                if prop.get("value") not in (None, "", [], {})
            }
            result[f"entity:{key}"] = {
                "ref": f"entity:{key}",
                "stableKey": key,
                "className": node["className"],
                "identity": identity,
                "displayProperties": {
                    **identity,
                    **display_properties,
                },
            }
    return result


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
    "stable_entity_key",
    "staged_entity_index",
    "workspace_chunks",
    "workspace_fingerprint",
]
