"""Per-batch ingestion tools: retrieval, schema selection, submission and repair."""

from typing import Any

from google.adk.tools import ToolContext

from app.agent.tools.ingestion_tools.shared import store_checkpoint, tool_exception
from app.agent.tools.ingestion_tools.state import (
    batch_result_payload,
    update_batch_accumulator,
)
from app.core.ingestion_runtime import get_service_container
from app.schemas import SemanticGraphPatchFragment, SemanticGraphRepairDelta
from app.services.ingestion.engine import operations
from app.services.ingestion.schema.semantic_validation.factory import (
    build_semantic_gate,
)
from app.utils import log_ingestion_event, stable_entity_key


async def get_ingestion_batch(
    ingestion_id: str, batch_index: int, tool_context: ToolContext
) -> dict[str, Any]:
    cache = tool_context.state.setdefault("ingestion_batch_cache", {})
    cache_key = f"{ingestion_id}:{batch_index}"
    if (cached := cache.get(cache_key)) is not None:
        return cached
    request = {"ingestion_id": ingestion_id, "batch_index": batch_index}
    try:
        container = await get_service_container()
        batch, chunks, canonical_nodes = await operations.get_batch(
            container.repository, ingestion_id, batch_index
        )
        canonical = [
            {
                "ref": f"entity:{stable_entity_key(node.class_name, node.identity)}",
                **node.model_dump(by_alias=True, mode="json"),
            }
            for node in canonical_nodes[:50]
        ]
        accumulator = tool_context.state.get("ingestion_batch_accumulator")
        if (
            isinstance(accumulator, dict)
            and accumulator.get("ingestionId") == ingestion_id
            and isinstance(accumulator.get("entities"), dict)
        ):
            canonical = [
                {"ref": ref, **node}
                for ref, node in list(accumulator["entities"].items())[:50]
            ]
        workspace = await operations.required_workspace(
            container.repository, ingestion_id
        )
        result = {
            "success": True,
            "stage": "batch_retrieved",
            "terminal": False,
            "nextAction": "load_scopes" if batch.scope_keys else "list_scopes",
            "ingestionId": ingestion_id,
            "batch": {
                "batchIndex": batch.batch_index,
                "scopeHint": workspace.job.scope_hint,
                "selectedScopeKeys": batch.scope_keys,
                "chunks": [
                    chunk.model_dump(by_alias=True, mode="json") for chunk in chunks
                ],
                "canonicalGraphContext": canonical,
            },
        }
        cache[cache_key] = result
        log_ingestion_event(
            f"GET_BATCH [idx={batch_index}]", payload=result, request=request
        )
        return result
    except Exception as exc:  # noqa: BLE001
        result = tool_exception("batch_retrieval", ingestion_id, exc)
        log_ingestion_event(
            f"GET_BATCH [idx={batch_index}]", payload=result, request=request
        )
        return result


async def list_ontology_scopes(
    ingestion_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    del tool_context
    request = {"ingestion_id": ingestion_id}
    try:
        container = await get_service_container()
        workspace = await operations.required_workspace(
            container.repository, ingestion_id
        )
        if (
            guarded := operations.workflow_guard_payload(workspace, "list_scopes")
        ) is not None:
            log_ingestion_event("LIST_SCOPES", payload=guarded, request=request)
            return guarded
        result = await operations.list_scopes(
            container.ontology_cache, str(workspace.job.ontology_version_id)
        )
        log_ingestion_event("LIST_SCOPES", payload=result, request=request)
        return result
    except Exception as exc:  # noqa: BLE001
        result = tool_exception("scope_catalog", ingestion_id, exc)
        log_ingestion_event("LIST_SCOPES", payload=result, request=request)
        return result


async def load_ontology_scopes(
    ingestion_id: str, scope_keys: list[str], tool_context: ToolContext
) -> dict[str, Any]:
    request = {"ingestion_id": ingestion_id, "scope_keys": scope_keys}
    try:
        container = await get_service_container()
        workspace = await operations.required_workspace(
            container.repository, ingestion_id
        )
        if (
            guarded := operations.workflow_guard_payload(workspace, "load_scopes")
        ) is not None:
            store_checkpoint(tool_context, guarded)
            log_ingestion_event(
                f"LOAD_SCOPES [{scope_keys}]", payload=guarded, request=request
            )
            return guarded
        result = await operations.load_scope(
            container.ontology_cache, scope_keys, str(workspace.job.ontology_version_id)
        )
        tool_context.state["active_ontology_scopes"] = scope_keys
        tool_context.state["ingestion_schema_context"] = {
            "ontologyVersionId": result["ontologyVersionId"],
            "scopeKeys": result["scopeKeys"],
            "schemaDigest": result["schemaDigest"],
        }
        store_checkpoint(tool_context, result)
        log_ingestion_event(
            f"LOAD_SCOPES [{scope_keys}]", payload=result, request=request
        )
        return result
    except Exception as exc:  # noqa: BLE001
        result = tool_exception("schema_load", ingestion_id, exc)
        store_checkpoint(tool_context, result)
        log_ingestion_event(
            f"LOAD_SCOPES [{scope_keys}]", payload=result, request=request
        )
        return result


def _log_payload(
    result: dict[str, Any], key: str, payload: dict[str, Any], workspace: Any | None
) -> dict[str, Any]:
    merged = {**result, key: payload}
    if result.get("success") and workspace is not None:
        batches = [
            item
            for item in workspace.batches
            if item.status == "STAGED" and item.graph_fragment
        ]
        if batches:
            merged["cumulativeGraph"] = {
                "stagedBatches": [item.batch_index for item in batches],
                "nodes": [
                    node
                    for item in batches
                    for node in item.graph_fragment.get("nodes", [])
                ],
                "edges": [
                    edge
                    for item in batches
                    for edge in item.graph_fragment.get("edges", [])
                ],
            }
    return merged


async def submit_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    scope_keys: list[str],
    extraction: SemanticGraphPatchFragment,
    tool_context: ToolContext,
) -> dict[str, Any]:
    payload = (
        extraction.model_dump(by_alias=True, mode="json")
        if hasattr(extraction, "model_dump")
        else extraction
        if isinstance(extraction, dict)
        else {}
    )
    request = {
        "ingestion_id": ingestion_id,
        "batch_index": batch_index,
        "scope_keys": scope_keys,
        "extraction_summary": {
            "nodes_count": len(payload.get("nodes", [])),
            "edges_count": len(payload.get("edges", [])),
            "coverage_count": len(payload.get("coverage", [])),
        },
    }
    try:
        container = await get_service_container()
        prior_fact_probe = getattr(container.graph_store, "has_committed_facts", None)
        workspace = await operations.submit_batch(
            container.repository,
            container.ontology_cache,
            ingestion_id,
            batch_index,
            scope_keys,
            extraction,
            prior_fact_available=(
                await prior_fact_probe() if prior_fact_probe is not None else False
            ),
            semantic_gate=build_semantic_gate(),
        )
        result = batch_result_payload(workspace, batch_index)
        tool_context.state["active_ingestion_id"] = ingestion_id
        staged = None
        if result.get("submittedBatchIndex") == batch_index and result.get("success"):
            staged = await operations.required_workspace(
                container.repository, ingestion_id
            )
            update_batch_accumulator(
                tool_context, ingestion_id, batch_index, result, staged
            )
        store_checkpoint(tool_context, result)
        log_ingestion_event(
            f"SUBMIT_BATCH [idx={batch_index}, scopes={scope_keys}]",
            payload=_log_payload(result, "extraction", payload, staged),
            request=request,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        result = tool_exception("batch_submission", ingestion_id, exc)
        store_checkpoint(tool_context, result)
        log_ingestion_event(
            f"SUBMIT_BATCH [idx={batch_index}]", payload=result, request=request
        )
        return result


async def repair_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    scope_keys: list[str],
    repair_delta: SemanticGraphRepairDelta,
    tool_context: ToolContext,
) -> dict[str, Any]:
    payload = repair_delta.model_dump(by_alias=True, mode="json")
    request = {
        "ingestion_id": ingestion_id,
        "batch_index": batch_index,
        "scope_keys": scope_keys,
        "delta_summary": {
            "nodes_count": len(payload.get("nodes", [])),
            "edges_count": len(payload.get("edges", [])),
            "coverage_count": len(payload.get("coverage", [])),
        },
    }
    try:
        container = await get_service_container()
        workspace = await operations.repair_batch(
            container.repository,
            container.ontology_cache,
            ingestion_id,
            batch_index,
            scope_keys,
            repair_delta,
        )
        result = batch_result_payload(workspace, batch_index)
        staged = None
        if result.get("submittedBatchIndex") == batch_index and result.get("success"):
            staged = await operations.required_workspace(
                container.repository, ingestion_id
            )
            update_batch_accumulator(
                tool_context, ingestion_id, batch_index, result, staged
            )
        store_checkpoint(tool_context, result)
        log_ingestion_event(
            f"REPAIR_BATCH_DELTA [idx={batch_index}, scopes={scope_keys}]",
            payload=_log_payload(result, "repairDelta", payload, staged),
            request=request,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        result = tool_exception("batch_repair", ingestion_id, exc)
        store_checkpoint(tool_context, result)
        log_ingestion_event(
            f"REPAIR_BATCH_DELTA [idx={batch_index}]", payload=result, request=request
        )
        return result


__all__ = [
    "get_ingestion_batch",
    "list_ontology_scopes",
    "load_ontology_scopes",
    "repair_ingestion_batch",
    "submit_ingestion_batch",
]
