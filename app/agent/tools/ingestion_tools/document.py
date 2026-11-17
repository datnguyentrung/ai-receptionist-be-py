"""Document lifecycle tools for ingestion."""

from typing import Any

from google.adk.tools import ToolContext

from app.agent.tools.ingestion_tools.shared import tool_error, tool_exception
from app.core.ingestion_runtime import get_service_container
from app.services.ingestion.engine import operations
from app.utils import log_ingestion_event


async def delete_document(
    document_id: str,
    tool_context: ToolContext,
    if_missing: str = "error",
) -> dict[str, Any]:
    """Deactivate a source document and its dependent graph assertions."""

    del tool_context
    if if_missing not in {"error", "ignore"}:
        return tool_error("delete", "INVALID_IF_MISSING", "Use 'error' or 'ignore'")
    request = {"document_id": document_id, "if_missing": if_missing}
    try:
        container = await get_service_container()
        result = await operations.delete_document(
            container.repository, container.graph_store, document_id, if_missing
        )
        log_ingestion_event(
            f"DELETE_DOCUMENT [{document_id}]", payload=result, request=request
        )
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary returns structured errors
        result = tool_exception("delete", None, exc)
        log_ingestion_event(
            f"DELETE_DOCUMENT [{document_id}]", payload=result, request=request
        )
        return result


async def rollback_document_version(
    document_id: str,
    version_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Roll a document back to an earlier source version."""

    del tool_context
    request = {"document_id": document_id, "version_id": version_id}
    try:
        container = await get_service_container()
        result = await operations.rollback_version(
            container.repository, container.graph_store, document_id, version_id
        )
        log_ingestion_event(
            f"ROLLBACK_VERSION [{document_id} -> {version_id}]",
            payload=result,
            request=request,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary returns structured errors
        result = tool_exception("rollback", None, exc)
        log_ingestion_event(
            f"ROLLBACK_VERSION [{document_id} -> {version_id}]",
            payload=result,
            request=request,
        )
        return result


__all__ = ["delete_document", "rollback_document_version"]
