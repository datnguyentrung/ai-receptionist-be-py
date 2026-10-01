"""ADK function tools for durable Taekwondo knowledge ingestion."""

from __future__ import annotations

from typing import Any

from google.adk.tools import ToolContext

from app.core.ingestion_runtime import get_service_container


async def begin_ingestion(
    artifact_name: str,
    tool_context: ToolContext,
    document_key: str | None = None,
    scope_hint: str | None = None,
) -> dict[str, Any]:
    """Prepare an uploaded PDF, DOCX, Markdown, or text artifact for batch ingestion."""
    artifact = await tool_context.load_artifact(artifact_name)
    if artifact is None or artifact.inline_data is None or artifact.inline_data.data is None:
        return _tool_error(
            "artifact_load",
            "ARTIFACT_NOT_FOUND",
            f"Artifact is unavailable in this ADK session: {artifact_name}",
        )
    result = await get_service_container().orchestrator.begin(
        artifact_name,
        bytes(artifact.inline_data.data),
        document_key=document_key,
        scope_hint=scope_hint,
        mime_type=artifact.inline_data.mime_type,
    )
    if result.get("ingestionId"):
        tool_context.state["active_ingestion_id"] = result["ingestionId"]
    return result


async def get_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Return the full chunks and prior canonical context for one batch."""
    del tool_context
    return await get_service_container().orchestrator.get_batch(ingestion_id, batch_index)


async def submit_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    graph_fragment: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Validate and persistently stage one source-grounded GraphPatchFragment."""
    result = await get_service_container().orchestrator.submit_batch(
        ingestion_id, batch_index, graph_fragment
    )
    tool_context.state["active_ingestion_id"] = ingestion_id
    tool_context.state["ingestion_checkpoint"] = {
        key: result.get(key)
        for key in ("stage", "nextAction", "nextBatch")
        if result.get(key) is not None
    }
    return result


async def finalize_ingestion(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Validate whole-document coverage and produce a readiness fingerprint."""
    result = await get_service_container().orchestrator.finalize(ingestion_id)
    tool_context.state["ingestion_checkpoint"] = {
        "stage": result.get("stage"),
        "repairBatchIndexes": result.get("repairBatchIndexes", []),
    }
    return result


async def fill_ingestion(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Explicitly promote a finalized ingestion into Neo4j and verify the write."""
    result = await get_service_container().orchestrator.fill(ingestion_id)
    tool_context.state["ingestion_checkpoint"] = {"stage": result.get("stage")}
    return result


async def get_ingestion_status(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Return durable progress for an ingestion, including after server restart."""
    del tool_context
    return await get_service_container().orchestrator.status(ingestion_id)


async def load_ontology_scope(
    scope_key: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Load a compact ACTIVE-ontology projection for the current batch."""
    del tool_context
    return await get_service_container().orchestrator.load_scope(scope_key)


async def validate_graph_patch(
    graph_patch: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Validate a caller-provided graph patch without persisting it."""
    del tool_context
    return await get_service_container().orchestrator.validate_patch(graph_patch)


async def fill_graph_patch(
    graph_patch: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Reject provenance-free direct writes and direct callers to document ingestion."""
    del graph_patch, tool_context
    return _tool_error(
        "persistence_precondition",
        "SOURCE_DOCUMENT_REQUIRED",
        "Direct graph writes are disabled. Upload a source artifact, run ingestion, "
        "finalize it, then explicitly call fill_ingestion.",
    )


async def delete_document(
    document_id: str,
    tool_context: ToolContext,
    if_missing: str = "error",
) -> dict[str, Any]:
    """Explicitly deactivate a document and unsupported graph facts."""
    del tool_context
    if if_missing not in {"error", "ignore"}:
        return _tool_error("delete", "INVALID_IF_MISSING", "Use 'error' or 'ignore'")
    return await get_service_container().orchestrator.delete_document(document_id, if_missing)


async def rollback_document_version(
    document_id: str,
    version_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Explicitly roll back one source version using persisted provenance."""
    del tool_context
    return await get_service_container().orchestrator.rollback_version(document_id, version_id)


INGESTION_TOOLS = (
    begin_ingestion,
    get_ingestion_batch,
    submit_ingestion_batch,
    finalize_ingestion,
    fill_ingestion,
    get_ingestion_status,
    load_ontology_scope,
    validate_graph_patch,
    fill_graph_patch,
    delete_document,
    rollback_document_version,
)


def get_ingestion_tools() -> list:
    return list(INGESTION_TOOLS)


def _tool_error(stage: str, code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "stage": stage,
        "terminal": True,
        "ingestionId": None,
        "nextAction": None,
        "errors": [{"code": code, "message": message}],
    }


__all__ = ["INGESTION_TOOLS", "get_ingestion_tools"]
