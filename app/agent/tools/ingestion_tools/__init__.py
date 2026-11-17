"""Public interface for ingestion workflow tools.

Keep callers on this package-level seam; implementations are grouped by responsibility.
"""

from app.agent.tools.ingestion_tools.batch import (
    get_ingestion_batch,
    list_ontology_scopes,
    load_ontology_scopes,
    repair_ingestion_batch,
    submit_ingestion_batch,
)
from app.agent.tools.ingestion_tools.document import (
    delete_document,
    rollback_document_version,
)
from app.agent.tools.ingestion_tools.registry import (
    INGESTION_BATCH_TOOLS,
    INGESTION_ROOT_TOOLS,
    INGESTION_TOOLS,
    get_ingestion_batch_tools,
    get_ingestion_root_tools,
    get_ingestion_tools,
)
from app.agent.tools.ingestion_tools.schema import (
    apply_schema_proposal,
    create_schema_proposal,
    get_schema_proposal,
    rebase_ingestion,
    review_schema_proposal,
)
from app.agent.tools.ingestion_tools.shared import store_checkpoint, tool_exception
from app.agent.tools.ingestion_tools.state import (
    batch_result_payload,
    repair_context,
    update_batch_accumulator,
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
from app.utils import log_ingestion_event

__all__ = [
    "INGESTION_BATCH_TOOLS",
    "INGESTION_ROOT_TOOLS",
    "INGESTION_TOOLS",
    "abort_ingestion",
    "apply_schema_proposal",
    "batch_result_payload",
    "begin_ingestion",
    "commit_ingestion",
    "create_schema_proposal",
    "delete_document",
    "finalize_ingestion",
    "get_ingestion_batch",
    "get_ingestion_batch_tools",
    "get_ingestion_preview",
    "get_ingestion_root_tools",
    "get_ingestion_status",
    "get_ingestion_tools",
    "get_schema_proposal",
    "list_ingestion_conflicts",
    "list_ontology_scopes",
    "load_ontology_scopes",
    "log_ingestion_event",
    "rebase_ingestion",
    "repair_context",
    "repair_ingestion_batch",
    "review_ingestion_conflict",
    "review_schema_proposal",
    "rollback_document_version",
    "store_checkpoint",
    "submit_ingestion_batch",
    "tool_exception",
    "update_batch_accumulator",
]
