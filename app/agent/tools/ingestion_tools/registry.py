"""Tool registration surfaces for the ingestion workflow."""

from app.agent.tools.ingestion_tools.batch import (
    get_ingestion_batch,
    list_ontology_scopes,
    load_ontology_scopes,
    repair_ingestion_batch,
    submit_ingestion_batch,
)
from app.agent.tools.ingestion_tools.schema import (
    apply_schema_proposal,
    create_schema_proposal,
    get_schema_proposal,
    rebase_ingestion,
    review_schema_proposal,
)
from app.agent.tools.ingestion_tools.workflow import (
    abort_ingestion,
    begin_ingestion,
    commit_ingestion,
    finalize_ingestion,
    get_ingestion_preview,
    get_ingestion_status,
    list_ingestion_conflicts,
    review_ingestion_conflict,
)

INGESTION_TOOLS = (
    begin_ingestion,
    get_ingestion_batch,
    submit_ingestion_batch,
    finalize_ingestion,
    get_ingestion_preview,
    commit_ingestion,
    abort_ingestion,
    get_ingestion_status,
    list_ingestion_conflicts,
    review_ingestion_conflict,
    list_ontology_scopes,
    load_ontology_scopes,
    create_schema_proposal,
    get_schema_proposal,
    review_schema_proposal,
    apply_schema_proposal,
    rebase_ingestion,
)

INGESTION_ROOT_TOOLS = (
    begin_ingestion,
    get_ingestion_status,
    create_schema_proposal,
    get_schema_proposal,
    review_schema_proposal,
    apply_schema_proposal,
    rebase_ingestion,
    finalize_ingestion,
    get_ingestion_preview,
    commit_ingestion,
    abort_ingestion,
    list_ingestion_conflicts,
    review_ingestion_conflict,
)

INGESTION_BATCH_TOOLS = (
    get_ingestion_batch,
    list_ontology_scopes,
    load_ontology_scopes,
    submit_ingestion_batch,
    repair_ingestion_batch,
)


def get_ingestion_tools() -> list:
    """Return the complete skill-managed tool list for ADK registration."""
    from app.agent.skills.ingestion.tools import get_tools

    return get_tools()


def get_ingestion_root_tools() -> list:
    return list(INGESTION_ROOT_TOOLS)


def get_ingestion_batch_tools() -> list:
    return list(INGESTION_BATCH_TOOLS)


__all__ = [
    "INGESTION_BATCH_TOOLS",
    "INGESTION_ROOT_TOOLS",
    "INGESTION_TOOLS",
    "get_ingestion_batch_tools",
    "get_ingestion_root_tools",
    "get_ingestion_tools",
]
