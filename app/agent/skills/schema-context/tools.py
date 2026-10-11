"""Tool registration for the reusable schema-context skill."""

from app.agent.tools.schema_context_tools import (
    list_active_schema_scopes,
    load_active_schema_context,
)


def get_tools() -> list:
    return [list_active_schema_scopes, load_active_schema_context]


__all__ = ["get_tools"]
