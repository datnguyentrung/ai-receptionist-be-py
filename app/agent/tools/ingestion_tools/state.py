"""Workspace-to-tool payload mapping and lightweight ADK session projections."""

from typing import Any

from google.adk.tools import ToolContext

from app.enums import IngestionJobStatus
from app.schemas import GraphNode, GraphPatchFragment, Workspace
from app.services.ingestion.engine import operations
from app.utils import stable_entity_key


def workspace_payload(
    workspace: Workspace, *, resumed: bool = False, idempotent: bool = False
) -> dict[str, Any]:
    pending = next(
        (item for item in workspace.batches if item.status != "STAGED"), None
    )
    if workspace.job.status == IngestionJobStatus.COMMITTED:
        stage, terminal, next_action = "committed", True, None
    elif workspace.job.status == IngestionJobStatus.FAILED:
        stage, terminal, next_action = (
            "explicit_extraction_failure",
            True,
            "explicit_extraction_failure",
        )
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
        stage, terminal, next_action = (
            "repair_required",
            False,
            "choose_ingestion_recovery",
        )
    else:
        stage, terminal, next_action = "batching", False, "process_batch"
    payload: dict[str, Any] = {
        "success": workspace.job.status != IngestionJobStatus.FAILED,
        "stage": stage,
        "terminal": terminal,
        "retryRequired": next_action == "choose_ingestion_recovery",
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
        payload["nextBatch"] = {
            "batchIndex": pending.batch_index,
            "chunkIndexes": pending.chunk_indexes,
            "selectedScopeKeys": pending.scope_keys,
        }
        if pending.status == "REPAIR_REQUIRED":
            payload["recoveryOptions"] = operations.recovery_options(pending)
    return payload


def repair_context(batch: Any) -> dict[str, Any] | None:
    if not batch.validated_baseline:
        return None
    baseline = GraphPatchFragment.model_validate(batch.validated_baseline)
    fingerprint = operations.baseline_fingerprint(baseline)
    return {
        "protectedBaseline": baseline.model_dump(by_alias=True, mode="json"),
        "baselineFingerprint": fingerprint,
        "validationIssues": batch.validation_issues,
        "deltaTemplate": {
            "baselineFingerprint": fingerprint,
            "nodes": [],
            "edges": [],
            "coverage": [],
        },
    }


def batch_result_payload(workspace: Workspace, batch_index: int) -> dict[str, Any]:
    batch = next(item for item in workspace.batches if item.batch_index == batch_index)
    if batch.status == "STAGED":
        return {
            **workspace_payload(workspace),
            "submittedBatchIndex": batch_index,
            "scopeKeys": batch.scope_keys,
            "mergedSchemaHash": batch.merged_schema_hash,
        }
    if batch.status == "FAILED":
        return {
            "success": False,
            "stage": "explicit_extraction_failure",
            "terminal": True,
            "retryRequired": False,
            "nextAction": "explicit_extraction_failure",
            "ingestionId": str(workspace.job.id),
            "batchIndex": batch_index,
            "affectedChunkIndexes": batch.chunk_indexes,
            "attempt": batch.validation_attempts,
            "errors": [
                {
                    "code": "BATCH_VALIDATION_RETRY_LIMIT_EXCEEDED",
                    "message": f"Batch {batch_index} exceeded the validation retry limit",
                },
                *batch.validation_issues,
            ],
        }
    codes = {issue.get("code") for issue in batch.validation_issues}
    schema_gap = bool(
        codes
        & {
            "SCHEMA_GAP_CANDIDATE",
            "UNKNOWN_ENTITY_TYPE",
            "UNKNOWN_PROPERTY",
            "UNKNOWN_RELATIONSHIP",
        }
    )
    scope_gap = "MISSING_SCOPE" in codes
    payload: dict[str, Any] = {
        "success": False,
        "stage": "schema_gap_candidate"
        if schema_gap
        else "scope_reselection_required"
        if scope_gap
        else "repair_required",
        "terminal": False,
        "retryRequired": not schema_gap,
        "nextAction": "assess_schema_gap"
        if schema_gap
        else "reselect_scopes"
        if scope_gap
        else "choose_ingestion_recovery",
        "ingestionId": str(workspace.job.id),
        "batchIndex": batch_index,
        "scopeKeys": batch.scope_keys,
        "affectedChunkIndexes": batch.chunk_indexes,
        "errors": batch.validation_issues,
        "validationAttempts": batch.validation_attempts,
        "maxAttempts": int(operations.MAX_BATCH_VALIDATION_ATTEMPTS),
    }
    context = repair_context(batch)
    if context is not None:
        payload["repairContext"] = context
    if batch.status == "REPAIR_REQUIRED":
        payload["recoveryOptions"] = operations.recovery_options(batch)
    return payload


def finalize_result_payload(workspace: Workspace) -> dict[str, Any]:
    incomplete = [
        item.batch_index for item in workspace.batches if item.status != "STAGED"
    ]
    if not incomplete:
        return workspace_payload(workspace)
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
        "ingestionId": str(workspace.job.id),
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


def update_batch_accumulator(
    tool_context: ToolContext,
    ingestion_id: str,
    batch_index: int,
    result: dict[str, Any],
    workspace: Workspace,
) -> None:
    if result.get("submittedBatchIndex") != batch_index or not result.get("success"):
        return
    accumulator = tool_context.state.get("ingestion_batch_accumulator")
    accumulator_dict = (
        dict(accumulator)
        if isinstance(accumulator, dict)
        and accumulator.get("ingestionId") == ingestion_id
        else {"ingestionId": ingestion_id, "batches": {}, "entities": {}}
    )
    batch = next(item for item in workspace.batches if item.batch_index == batch_index)
    batches = accumulator_dict.setdefault("batches", {})
    if isinstance(batches, dict):
        batches[str(batch_index)] = {
            "status": "STAGED",
            "batchIndex": batch_index,
            "scopeKeys": batch.scope_keys,
            "nextBatch": result.get("nextBatch"),
        }
    entities: dict[str, dict[str, Any]] = {}
    for staged in workspace.batches:
        if staged.status != "STAGED" or not staged.graph_fragment:
            continue
        for raw_node in staged.graph_fragment.get("nodes", []):
            node = GraphNode.model_validate(raw_node)
            entities[f"entity:{stable_entity_key(node.class_name, node.identity)}"] = (
                node.model_dump(by_alias=True, mode="json")
            )
            if len(entities) >= 50:
                break
        if len(entities) >= 50:
            break
    accumulator_dict["entities"] = entities
    tool_context.state["ingestion_batch_accumulator"] = accumulator_dict


def rebuild_batch_accumulator(
    tool_context: ToolContext, ingestion_id: str, workspace: Workspace
) -> None:
    tool_context.state["ingestion_batch_accumulator"] = {
        "ingestionId": ingestion_id,
        "batches": {},
        "entities": {},
    }
    for batch in workspace.batches:
        if batch.status == "STAGED":
            update_batch_accumulator(
                tool_context,
                ingestion_id,
                batch.batch_index,
                {"submittedBatchIndex": batch.batch_index, "success": True},
                workspace,
            )


__all__ = [
    "batch_result_payload",
    "finalize_result_payload",
    "rebuild_batch_accumulator",
    "repair_context",
    "update_batch_accumulator",
    "workspace_payload",
]
