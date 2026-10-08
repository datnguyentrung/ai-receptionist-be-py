"""Nhóm động cơ điều phối và lưu trữ Ingestion (Ingestion Engine & Storage).

Danh sách các module trong package:
- `operations`: Điều phối toàn bộ các nghiệp vụ nạp tri thức và tương tác agent.
- `repository`: Quản lý phiên làm việc in-memory và trạng thái jobs ingestion.
- `workflow_policy`: Chính sách kiểm soát máy trạng thái và quyền gọi hành động của agent.
- `graph_store`: Adapter lưu trữ và đồng bộ dữ liệu vào Neo4j cho Taekwondo GraphRAG.
"""

from app.services.ingestion.engine.graph_store import (
    CHUNK_FULLTEXT_INDEX,
    CHUNK_VECTOR_INDEX,
    ENTITY_FULLTEXT_INDEX,
    FACT_VECTOR_INDEX,
    Neo4jIngestionStore,
)
from app.services.ingestion.engine.operations import (
    begin,
    delete_document,
    fill,
    finalize,
    get_batch,
    list_scopes,
    load_scope,
    repair_batch,
    rollback_version,
    submit_batch,
    validate_patch,
)
from app.services.ingestion.engine.repository import (
    IngestionRepository,
    Workspace,
)
from app.utils.ingestion_helpers import (
    stable_entity_key,
    staged_entity_index,
    workspace_chunks,
    workspace_fingerprint,
)

from app.services.ingestion.engine.workflow_policy import (
    TERMINAL_NEXT_ACTION,
    WORKFLOW_POLICY,
    WorkflowDecision,
    evaluate_workflow,
    normalize_status,
)

__all__ = [
    "CHUNK_FULLTEXT_INDEX",
    "CHUNK_VECTOR_INDEX",
    "ENTITY_FULLTEXT_INDEX",
    "FACT_VECTOR_INDEX",
    "TERMINAL_NEXT_ACTION",
    "WORKFLOW_POLICY",
    "IngestionRepository",
    "Neo4jIngestionStore",
    "WorkflowDecision",
    "Workspace",
    "begin",
    "delete_document",
    "evaluate_workflow",
    "fill",
    "finalize",
    "get_batch",
    "list_scopes",
    "load_scope",
    "repair_batch",
    "normalize_status",
    "rollback_version",
    "stable_entity_key",
    "staged_entity_index",
    "submit_batch",
    "validate_patch",
    "workspace_chunks",
    "workspace_fingerprint",
]
