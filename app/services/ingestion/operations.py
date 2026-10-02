"""Deterministic primitives exposed by the ingestion ADK tools.

The semantic control flow intentionally lives in the ingestion SKILL.md.
Phân định vai trò lưu trữ:
- PostgreSQL: Lưu trữ bền vững ontology/schema, compiled snapshots và schema proposals.
- RAM (IngestionRepository): Quản lý trạng thái workspace, job, batches và chunks trong tiến trình hiện tại.
- Neo4j: Lưu trữ bền vững đồ thị tri thức (Knowledge Graph) sau khi nạp chính thức.
"""

import hashlib
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.schemas.ingestion_schema import (
    GraphPatchFragment,
    IngestionJobStatus,
    PreparedChunk,
    SourceVersionStatus,
)
from app.services.ingestion.document.reader import DocumentReader
from app.services.ingestion.graph_store import Neo4jIngestionStore
from app.services.ingestion.ontology import (
    COMPILER_VERSION,
    OntologyCache,
    OntologyRegistry,
)
from app.services.ingestion.repository import (
    IngestionRepository,
    Workspace,
    workspace_chunks,
    workspace_fingerprint,
)


async def begin(
    repository: IngestionRepository,
    ontology_cache: OntologyCache,
    artifact_name: str,
    data: bytes,
    *,
    max_file_size: int,
    chunk_size_chars: int,
    batch_size: int,
    skill_digest: str,
    model_id: str,
    document_key: str | None = None,
    scope_hint: str | None = None,
    mime_type: str | None = None,
) -> dict[str, Any]:
    """Chuẩn bị tài liệu qua DocumentReader và khởi tạo mới hoặc tái sử dụng workspace ingestion trong RAM."""
    if len(data) > max_file_size:
        raise ValueError(f"Artifact exceeds the {max_file_size}-byte ingestion limit")
    reader = DocumentReader()
    chunks = reader.read_bytes(
        filename=artifact_name,
        data=data,
        mime_type=mime_type,
    )
    content_hash = hashlib.sha256(data).hexdigest()
    ontology = await ontology_cache.active_version()
    workspace, resumed, committed = await repository.create_or_resume(
        artifact_name=artifact_name,
        content_hash=content_hash,
        chunks=chunks,
        ontology=ontology,
        document_key=document_key or _document_key(artifact_name),
        scope_hint=scope_hint,
        skill_digest=skill_digest,
        model_id=model_id,
        compiler_version=COMPILER_VERSION,
    )
    return status_payload(workspace, resumed=resumed or committed, idempotent=committed)


async def get_batch(
    repository: IngestionRepository,
    ingestion_id: str,
    batch_index: int,
) -> dict[str, Any]:
    workspace = await required_workspace(repository, ingestion_id)
    if workspace.job.status in {
        IngestionJobStatus.COMMITTED,
        IngestionJobStatus.FAILED,
    }:
        return status_payload(workspace)
    batch = next(
        (item for item in workspace.batches if item.batch_index == batch_index), None
    )
    if batch is None:
        return error_payload(
            "batch_validation", ingestion_id, "INVALID_BATCH_INDEX", str(batch_index)
        )
    chunks = workspace_chunks(workspace, batch.chunk_indexes)

    # Không chọn lại scope nếu đã chọn rồi.
    next_action = "load_scopes" if batch.scope_keys else "list_scopes"

    return {
        "success": True,
        "stage": "batch_retrieved",
        "terminal": False,
        "nextAction": next_action,
        "ingestionId": ingestion_id,
        "batch": {
            "batchIndex": batch.batch_index,
            "scopeHint": workspace.job.scope_hint,
            "selectedScopeKeys": batch.scope_keys,
            "chunks": [item.model_dump(by_alias=True, mode="json") for item in chunks],
            "canonicalGraphContext": _canonical_context(workspace, batch_index),
        },
    }


async def submit_batch(
    repository: IngestionRepository,
    ontology_cache: OntologyCache,
    ingestion_id: str,
    batch_index: int,
    scope_keys: list[str],
    graph_fragment: GraphPatchFragment,
) -> dict[str, Any]:
    workspace = await required_workspace(repository, ingestion_id)
    batch = next(
        (item for item in workspace.batches if item.batch_index == batch_index), None
    )
    if batch is None:
        return error_payload(
            "batch_validation", ingestion_id, "INVALID_BATCH_INDEX", str(batch_index)
        )

    if (
        batch.status == "REPAIR_REQUIRED"
        and batch.scope_keys
        and set(scope_keys) != set(batch.scope_keys)
    ):
        return error_payload(
            "batch_validation",
            ingestion_id,
            "REPAIR_SCOPE_MISMATCH",
            "Repair must reuse the previously selected scope set unless "
            "the workflow explicitly reselects scopes.",
        )

    projection = await ontology_cache.get_many(
        scope_keys, str(workspace.job.ontology_version_id)
    )
    bindings = [
        {
            "scopeKey": key,
            "schemaHash": (
                await ontology_cache.get(key, str(workspace.job.ontology_version_id))
            ).digest,
        }
        for key in projection.scope_keys
    ]
    try:
        fragment = (
            graph_fragment
            if isinstance(graph_fragment, GraphPatchFragment)
            else GraphPatchFragment.model_validate(graph_fragment)
        )
    except ValidationError as exc:
        issues = [
            {
                "code": "INVALID_GRAPH_PATCH",
                "message": item["msg"],
                "location": ".".join(str(part) for part in item["loc"]),
                "retryable": True,
            }
            for item in exc.errors()
        ]
        workspace = await repository.store_batch_result(
            ingestion_id, batch_index, bindings, projection.digest, None, issues
        )
        return batch_failure_payload(workspace, batch_index, issues)

    issues = [
        item.model_dump(by_alias=True, mode="json")
        for item in OntologyRegistry(projection).validate_fragment(
            fragment, workspace_chunks(workspace, batch.chunk_indexes)
        )
    ]
    if issues:
        workspace = await repository.store_batch_result(
            ingestion_id, batch_index, bindings, projection.digest, fragment, issues
        )
        result = batch_failure_payload(workspace, batch_index, issues)
        if any(
            item["code"]
            in {"UNKNOWN_ENTITY_TYPE", "UNKNOWN_PROPERTY", "UNKNOWN_RELATIONSHIP"}
            for item in issues
        ):
            result["stage"] = "schema_gap_candidate"
            result["nextAction"] = "assess_schema_gap"
        return result

    workspace = await repository.store_batch_result(
        ingestion_id, batch_index, bindings, projection.digest, fragment, []
    )
    result = status_payload(workspace)
    result["submittedBatchIndex"] = batch_index
    result["scopeKeys"] = projection.scope_keys
    result["mergedSchemaHash"] = projection.digest
    return result


async def finalize(
    repository: IngestionRepository, ingestion_id: str
) -> dict[str, Any]:
    workspace = await required_workspace(repository, ingestion_id)
    incomplete = [
        item.batch_index for item in workspace.batches if item.status != "STAGED"
    ]
    if incomplete:
        blocked = [
            item.batch_index
            for item in workspace.batches
            if item.status == "BLOCKED_SCHEMA"
        ]
        return {
            "success": False,
            "stage": "awaiting_schema_approval" if blocked else "repair_required",
            "terminal": False,
            "nextAction": "wait_for_schema_review" if blocked else "repair_batches",
            "ingestionId": ingestion_id,
            "repairBatchIndexes": incomplete,
            "errors": [
                {
                    "code": "INCOMPLETE_BATCHES",
                    "message": f"Batches require schema review: {blocked}"
                    if blocked
                    else f"Batches require repair: {incomplete}",
                    "location": "batches",
                    "retryable": True,
                }
            ],
        }
    return status_payload(
        await repository.mark_ready(ingestion_id, workspace_fingerprint(workspace))
    )


async def fill(
    repository: IngestionRepository,
    graph_store: Neo4jIngestionStore,
    ingestion_id: str,
) -> dict[str, Any]:
    """Ghi chính thức tri thức vào Neo4j và cập nhật trạng thái COMMITTED cho workspace trong bộ nhớ RAM."""
    workspace = await required_workspace(repository, ingestion_id)
    if workspace.job.status == IngestionJobStatus.COMMITTED:
        return status_payload(workspace, idempotent=True)
    expected = workspace_fingerprint(workspace)
    if (
        workspace.job.status != IngestionJobStatus.READY
        or workspace.job.readiness_fingerprint != expected
    ):
        return error_payload(
            "validation_precondition",
            ingestion_id,
            "VALIDATION_PRECONDITION",
            "finalize_ingestion must succeed before fill_ingestion",
        )
    await repository.mark_writing(ingestion_id)
    try:
        result = await graph_store.fill(workspace)
        previous_version_id = workspace.document.current_version_id
        if previous_version_id and previous_version_id != workspace.version.id:
            result["superseded"] = await graph_store.deactivate_version(
                str(previous_version_id), "SUPERSEDED"
            )
        committed = await repository.mark_committed(ingestion_id, result)
        return {**status_payload(committed), **result}
    except Exception as exc:  # noqa: BLE001 - reconcile cross-store commit
        reconciled = await graph_store.committed_summary(str(workspace.version.id))
        if reconciled is not None:
            committed = await repository.mark_committed(ingestion_id, reconciled)
            return {**status_payload(committed), **reconciled}
        await repository.mark_failed(ingestion_id, "persistence_failure", str(exc))
        return error_payload(
            "persistence_failure", ingestion_id, "PERSISTENCE_FAILED", str(exc)
        )


async def list_scopes(
    ontology_cache: OntologyCache, ontology_version_id: str | None = None
) -> dict[str, Any]:
    ontology = await ontology_cache.active_version()
    version_id = ontology_version_id or ontology.version_id
    scopes = await ontology_cache.list_scopes(version_id)
    return {
        "success": True,
        "stage": "scope_catalog_loaded",
        "ontologyVersionId": version_id,
        "ontologyVersion": ontology.version
        if version_id == ontology.version_id
        else None,
        "scopes": [item.model_dump(by_alias=True, mode="json") for item in scopes],
    }


async def load_scope(
    ontology_cache: OntologyCache,
    scope_keys: list[str],
    ontology_version_id: str,
) -> dict[str, Any]:
    projection = await ontology_cache.get_many(scope_keys, ontology_version_id)
    return {
        "success": True,
        "stage": "schema_loaded",
        "terminal": False,
        "ingestionId": None,
        "nextAction": "extract_batch",
        "scope": projection.model_dump(by_alias=True, mode="json"),
    }


async def validate_patch(
    ontology_cache: OntologyCache,
    graph_patch: dict[str, Any],
    scope_keys: list[str],
    ontology_version_id: str,
) -> dict[str, Any]:
    try:
        fragment = GraphPatchFragment.model_validate(graph_patch)
    except ValidationError as exc:
        return {
            "success": False,
            "stage": "graph_validation",
            "terminal": True,
            "ingestionId": None,
            "nextAction": None,
            "errors": exc.errors(include_url=False),
        }
    projection = await ontology_cache.get_many(scope_keys, ontology_version_id)
    issues = OntologyRegistry(projection).validate_fragment(
        fragment, _chunks_from_evidence(fragment)
    )
    return {
        "success": not issues,
        "stage": "graph_validation",
        "terminal": True,
        "ingestionId": None,
        "nextAction": None,
        "valid": not issues,
        "scopeKeys": projection.scope_keys,
        "errors": [item.model_dump(by_alias=True, mode="json") for item in issues],
    }


async def delete_document(
    repository: IngestionRepository,
    graph_store: Neo4jIngestionStore,
    document_id: str,
    if_missing: str,
) -> dict[str, Any]:
    try:
        document, version = await repository.set_document_version_status(
            document_id, None, SourceVersionStatus.DELETED
        )
    except KeyError:
        if if_missing == "ignore":
            return {
                "success": True,
                "stage": "deleted",
                "terminal": True,
                "ingestionId": None,
                "nextAction": None,
                "documentId": document_id,
                "missing": True,
            }
        return error_payload("delete", None, "DOCUMENT_NOT_FOUND", document_id)
    result = await graph_store.deactivate_version(str(version.id), "DELETED")
    return {
        "success": True,
        "stage": "deleted",
        "terminal": True,
        "ingestionId": None,
        "nextAction": None,
        "documentId": str(document.id),
        **result,
    }


async def rollback_version(
    repository: IngestionRepository,
    graph_store: Neo4jIngestionStore,
    document_id: str,
    version_id: str,
) -> dict[str, Any]:
    try:
        document, version = await repository.set_document_version_status(
            document_id, version_id, SourceVersionStatus.ROLLED_BACK
        )
    except KeyError:
        return error_payload("rollback", None, "VERSION_NOT_FOUND", version_id)
    result = await graph_store.deactivate_version(str(version.id), "ROLLED_BACK")
    return {
        "success": True,
        "stage": "rolled_back",
        "terminal": True,
        "ingestionId": None,
        "nextAction": None,
        "documentId": str(document.id),
        **result,
    }


async def required_workspace(
    repository: IngestionRepository, ingestion_id: str
) -> Workspace:
    workspace = await repository.get_workspace(ingestion_id)
    if workspace is None:
        raise KeyError(f"Unknown ingestionId: {ingestion_id}")
    return workspace


def batch_failure_payload(
    workspace: Workspace, batch_index: int, issues: list[dict]
) -> dict[str, Any]:
    batch = next(item for item in workspace.batches if item.batch_index == batch_index)
    return {
        "success": False,
        "stage": "repair_required",
        "terminal": False,
        "nextAction": "repair_batch",
        "ingestionId": str(workspace.job.id),
        "batchIndex": batch_index,
        "scopeKeys": getattr(batch, "scope_keys", []),  # phạm vi đã dùng
        "snapshotHashes": getattr(batch, "snapshot_hashes", {}),  # snapshot đã dùng
        "mergedSchemaHash": getattr(
            batch, "merged_schema_hash", None
        ),  # lược đồ hợp nhất đã dùng
        "graphFragment": getattr(batch, "graph_fragment", None),
        "errors": issues,
        "validationAttempts": getattr(batch, "validation_attempts", 0),
    }


def status_payload(
    workspace: Workspace,
    *,
    resumed: bool = False,
    idempotent: bool = False,
) -> dict[str, Any]:
    pending = next(
        (item for item in workspace.batches if item.status != "STAGED"), None
    )
    if workspace.job.status == IngestionJobStatus.COMMITTED:
        stage, terminal, next_action = "committed", True, None
    elif workspace.job.status == IngestionJobStatus.FAILED:
        stage, terminal, next_action = "failed", True, None
    elif workspace.job.status == IngestionJobStatus.READY:
        stage, terminal, next_action = "ready_to_fill", False, "fill"
    elif pending is None:
        stage, terminal, next_action = "ready_to_finalize", False, "finalize"
    elif pending.status == "BLOCKED_SCHEMA":
        stage, terminal, next_action = (
            "awaiting_schema_approval",
            False,
            "wait_for_schema_review",
        )
    elif pending.status == "REPAIR_REQUIRED":
        stage, terminal, next_action = "repair_required", False, "repair_batch"
    else:
        stage, terminal, next_action = "batching", False, "process_batch"
    result: dict[str, Any] = {
        "success": workspace.job.status != IngestionJobStatus.FAILED,
        "stage": stage,
        "terminal": terminal,
        "nextAction": next_action,
        "ingestionId": str(workspace.job.id),
        "documentId": str(workspace.document.id),
        "documentVersionId": str(workspace.version.id),
        "ontologyVersionId": str(workspace.version.ontology_version_id),
        "workspaceStats": {
            "chunks": len(workspace.chunks),
            "batches": len(workspace.batches),
            "stagedBatches": sum(item.status == "STAGED" for item in workspace.batches),
        },
        "resumed": resumed,
        "idempotent": idempotent,
    }
    if pending:
        result["nextBatch"] = {
            "batchIndex": pending.batch_index,
            "chunkIndexes": pending.chunk_indexes,
            "selectedScopeKeys": pending.scope_keys,
        }
    if workspace.job.error_message:
        result["errors"] = [
            {
                "code": "INGESTION_FAILED",
                "message": workspace.job.error_message,
                "location": workspace.job.error_stage,
            }
        ]
    return result


def error_payload(
    stage: str,
    ingestion_id: str | None,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "success": False,
        "stage": stage,
        "terminal": True,
        "ingestionId": ingestion_id,
        "nextAction": None,
        "errors": [{"code": code, "message": message}],
    }


def _document_key(filename: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", Path(filename).stem.casefold()).strip("-")
    return stem or hashlib.sha256(filename.encode("utf-8")).hexdigest()[:24]


def _canonical_context(workspace: Workspace, before_batch: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for batch in workspace.batches:
        if batch.batch_index >= before_batch or not batch.graph_fragment:
            continue
        result.extend(
            {
                "tempId": node["tempId"],
                "className": node["className"],
                "identity": node.get("identity") or {},
            }
            for node in batch.graph_fragment.get("nodes", [])
        )
    return result[:50]


def _chunks_from_evidence(fragment: GraphPatchFragment) -> list[PreparedChunk]:
    texts: dict[int, list[str]] = {}
    evidence_items = [e for node in fragment.nodes for e in node.evidence]
    evidence_items.extend(
        e for node in fragment.nodes for prop in node.properties for e in prop.evidence
    )
    evidence_items.extend(e for edge in fragment.edges for e in edge.evidence)
    for evidence in evidence_items:
        texts.setdefault(evidence.chunk_index, []).append(evidence.text)
    return [
        PreparedChunk(
            chunk_id=f"direct-{index}",
            chunk_index=index,
            text="\n".join(texts.get(index, [])),
            content_hash=hashlib.sha256(
                "\n".join(texts.get(index, [])).encode()
            ).hexdigest(),
            token_count=max(1, len("\n".join(texts.get(index, []))) // 4),
            source_anchor=f"direct-patch#chunk-{index}",
        )
        for index in sorted(
            {item.chunk_index for item in fragment.coverage} | set(texts)
        )
    ]


__all__ = [
    "begin",
    "delete_document",
    "error_payload",
    "fill",
    "finalize",
    "get_batch",
    "list_scopes",
    "load_scope",
    "required_workspace",
    "rollback_version",
    "status_payload",
    "submit_batch",
    "validate_patch",
]
