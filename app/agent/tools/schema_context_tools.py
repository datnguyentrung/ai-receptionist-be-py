"""Read-only ADK tools for resolving Graph QA schema context."""

from typing import Any

from google.adk.tools import ToolContext

from app.core.ingestion_runtime import get_service_container


async def list_active_schema_scopes(tool_context: ToolContext) -> dict[str, Any]:
    """List the catalog for the currently ACTIVE ontology only."""

    container = await get_service_container()
    active = await container.ontology_cache.active_version()
    result = await container.schema_context.list_scopes(active.version_id)
    tool_context.state["graph_qa_ontology_version_id"] = active.version_id
    return {"success": True, **result}


async def load_active_schema_context(
    scope_keys: list[str], tool_context: ToolContext
) -> dict[str, Any]:
    """Load compiled schema from the currently ACTIVE ontology only."""

    container = await get_service_container()
    result = await container.schema_context.active_context(scope_keys)
    tool_context.state["graph_qa_schema_context"] = {
        "ontologyVersionId": result["ontologyVersionId"],
        "scopeKeys": result["scopeKeys"],
        "schemaDigest": result["schemaDigest"],
    }
    return {"success": True, **result}


__all__ = ["list_active_schema_scopes", "load_active_schema_context"]
