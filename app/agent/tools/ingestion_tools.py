"""Các công cụ (ADK Function Tools) phục vụ quy trình nạp và trích xuất tri thức Taekwondo bền vững.

Mỗi tool ánh xạ tới đúng một primitive deterministic. Quy trình semantic và thứ tự gọi
tool thuộc quyền sở hữu của ingestion SKILL.md.
"""

from typing import Any

from google.adk.tools import ToolContext

from app.core.ingestion_runtime import get_service_container
from app.schemas import GraphPatchFragment
from app.services.ingestion import operations
from app.utils.ingestion_logger import log_ingestion_event


async def begin_ingestion(
    artifact_name: str,
    tool_context: ToolContext,
    document_key: str | None = None,
    scope_hint: str | None = None,
) -> dict[str, Any]:
    """Khởi tạo mới hoặc tái sử dụng phiên ingestion còn tồn tại trong tiến trình hiện tại (RAM).

    Công dụng:
        - Tải file nhị phân của artifact từ phiên làm việc ADK (ToolContext).
        - Parse tài liệu, làm sạch, băm nhỏ thành chunks và nhóm thành batches.
        - Khởi tạo mới hoặc tái sử dụng Ingestion Workspace trong bộ nhớ RAM của tiến trình hiện tại.
        - Lưu `ingestionId` vào trạng thái phiên (`tool_context.state`) để duy trì ngữ cảnh.

    Tham số:
        artifact_name: Tên của artifact/tài liệu được tải lên trong session.
        tool_context: Ngữ cảnh thực thi tool của ADK (chứa state và artifact loader).
        document_key: Khóa định danh tài liệu duy nhất (tùy chọn; mặc định sinh từ tên file).
        scope_hint: Gợi ý không ràng buộc; tác nhân vẫn phải chọn từ catalog động.

    Trả về:
        Dictionary chứa thông tin phiên nạp: `ingestionId`, `stage`, `totalBatches`, `nextBatch`...
    """
    try:
        # 1. Tải dữ liệu artifact từ phiên làm việc ADK hiện tại
        artifact = await tool_context.load_artifact(artifact_name)
        if (
            artifact is None
            or artifact.inline_data is None
            or artifact.inline_data.data is None
        ):
            err = _tool_error(
                "artifact_load",
                "ARTIFACT_NOT_FOUND",
                f"Artifact is unavailable in this ADK session: {artifact_name}",
            )
            log_ingestion_event(f"BEGIN [{artifact_name}]", payload=err)
            return err

        container = await get_service_container()
        result = await operations.begin(
            container.repository,
            container.ontology_cache,
            artifact_name,
            bytes(artifact.inline_data.data),
            max_file_size=container.max_file_size,
            chunk_size_chars=container.chunk_size_chars,
            batch_size=container.batch_size,
            skill_digest=container.skill_digest,
            model_id=container.model_id,
            document_key=document_key,
            scope_hint=scope_hint,
            mime_type=artifact.inline_data.mime_type,
        )

        # 3. Ghi nhận ID phiên nạp vào ADK State để các bước tiếp theo tự động kế thừa
        if result.get("ingestionId"):
            tool_context.state["active_ingestion_id"] = result["ingestionId"]
        log_ingestion_event(f"BEGIN [{artifact_name}]", payload=result)
        return result
    except Exception as exc:
        log_ingestion_event(f"BEGIN [{artifact_name}]", error=str(exc))
        raise


async def get_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Lấy nội dung chi tiết các đoạn văn bản (chunks) và ngữ cảnh đã có của một batch."""
    del tool_context
    try:
        container = await get_service_container()
        result = await operations.get_batch(
            container.repository, ingestion_id, batch_index
        )
        log_ingestion_event(f"GET_BATCH [idx={batch_index}]", payload=result)
        return result
    except Exception as exc:
        log_ingestion_event(f"GET_BATCH [idx={batch_index}]", error=str(exc))
        raise


async def submit_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    scope_keys: list[str],
    graph_fragment: GraphPatchFragment,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Xác thực và lưu tạm (stage) một mảnh đồ thị tri thức (GraphPatchFragment) cho batch."""
    try:
        container = await get_service_container()
        result = await operations.submit_batch(
            container.repository,
            container.ontology_cache,
            ingestion_id,
            batch_index,
            scope_keys,
            graph_fragment,
        )

        tool_context.state["active_ingestion_id"] = ingestion_id
        tool_context.state["ingestion_checkpoint"] = {
            key: result.get(key)
            for key in (
                "stage",
                "nextAction",
                "nextBatch",
                "batchIndex",
                "scopeKeys",
                "snapshotHashes",
                "mergedSchemaHash",
                "graphFragment",
                "errors",
                "validationAttempts",
            )
            if result.get(key) is not None
        }
        log_ingestion_event(
            f"SUBMIT_BATCH [idx={batch_index}, scopes={scope_keys}]", payload=result
        )
        return result
    except Exception as exc:
        log_ingestion_event(f"SUBMIT_BATCH [idx={batch_index}]", error=str(exc))
        raise


async def finalize_ingestion(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Kiểm tra độ phủ toàn bộ tài liệu và tạo chữ ký sẵn sàng (readiness fingerprint)."""
    try:
        container = await get_service_container()
        result = await operations.finalize(container.repository, ingestion_id)
        tool_context.state["ingestion_checkpoint"] = {
            "stage": result.get("stage"),
            "repairBatchIndexes": result.get("repairBatchIndexes", []),
        }
        log_ingestion_event("FINALIZE", payload=result)
        return result
    except Exception as exc:
        log_ingestion_event("FINALIZE", error=str(exc))
        raise


async def fill_ingestion(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Ghi chính thức tri thức đã hoàn tất nạp vào Neo4j, cập nhật trạng thái COMMITTED trong RAM và thực hiện đọc kiểm chứng (read-back)."""
    try:
        container = await get_service_container()
        result = await operations.fill(
            container.repository, container.graph_store, ingestion_id
        )
        tool_context.state["ingestion_checkpoint"] = {"stage": result.get("stage")}
        log_ingestion_event("FILL_COMMIT_TO_NEO4J", payload=result)
        return result
    except Exception as exc:
        log_ingestion_event("FILL_COMMIT_TO_NEO4J", error=str(exc))
        raise


async def get_ingestion_status(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Truy vấn tiến độ và trạng thái hiện tại của một tiến trình nạp tài liệu."""
    del tool_context
    container = await get_service_container()
    workspace = await operations.required_workspace(container.repository, ingestion_id)
    return operations.status_payload(workspace)


async def list_ontology_scopes(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Liệt kê danh mục scope nhẹ của đúng ontology version đã ghim cho ingestion."""
    del tool_context
    container = await get_service_container()
    workspace = await operations.required_workspace(container.repository, ingestion_id)
    return await operations.list_scopes(
        container.ontology_cache, str(workspace.job.ontology_version_id)
    )


async def load_ontology_scopes(
    ingestion_id: str,
    scope_keys: list[str],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Tải và hợp nhất deterministic các compiled snapshots đã chọn cho một batch."""
    container = await get_service_container()
    workspace = await operations.required_workspace(container.repository, ingestion_id)
    result = await operations.load_scope(
        container.ontology_cache, scope_keys, str(workspace.job.ontology_version_id)
    )
    tool_context.state["active_ontology_scopes"] = scope_keys
    return result


async def create_schema_proposal(
    ingestion_id: str,
    batch_index: int,
    proposal_type: str,
    reason: str,
    payload: dict[str, Any],
    evidence: dict[str, Any],
    affected_scope_keys: list[str],
    tool_context: ToolContext,
    technical_name: str | None = None,
) -> dict[str, Any]:
    """Lưu bền vững đề xuất thay đổi lược đồ vào PostgreSQL và tạm chặn batch nguồn trong bộ nhớ RAM."""
    container = await get_service_container()
    workspace = await operations.required_workspace(container.repository, ingestion_id)
    proposal = await container.ontology_lifecycle.create_proposal(
        ingestion_id=ingestion_id,
        batch_index=batch_index,
        ontology_version_id=str(workspace.job.ontology_version_id),
        source_document_id=str(workspace.version.id),
        proposal_type=proposal_type,
        technical_name=technical_name,
        reason=reason,
        payload=payload,
        evidence=evidence,
        affected_scope_keys=affected_scope_keys,
    )
    await container.repository.block_batch_for_proposal(
        ingestion_id,
        batch_index,
        [
            {
                "code": "SCHEMA_PROPOSAL_PENDING",
                "message": f"Schema proposal {proposal.id} is waiting for review",
                "location": f"batch[{batch_index}]",
                "retryable": True,
            }
        ],
    )
    tool_context.state["pending_schema_proposal_id"] = str(proposal.id)
    return {
        "success": True,
        "stage": "awaiting_schema_approval",
        "nextAction": "wait_for_user_review",
        "ingestionId": ingestion_id,
        "batchIndex": batch_index,
        "proposalId": str(proposal.id),
        "proposalStatus": proposal.status.value,
    }


async def get_schema_proposal(
    proposal_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    """Đọc thông tin và trạng thái bền vững của đề xuất lược đồ từ PostgreSQL."""
    del tool_context
    container = await get_service_container()
    proposal = await container.ontology_lifecycle.get_proposal(proposal_id)
    if proposal is None:
        return _tool_error("schema_proposal", "PROPOSAL_NOT_FOUND", proposal_id)
    return {
        "success": True,
        "stage": "schema_proposal",
        "proposalId": str(proposal.id),
        "proposalType": proposal.proposal_type.value,
        "proposalStatus": proposal.status.value,
        "reason": proposal.reason,
        "payload": proposal.payload,
        "evidence": proposal.evidence,
        "affectedScopeKeys": proposal.affected_scope_keys,
        "appliedOntologyVersionId": str(proposal.applied_ontology_version_id)
        if proposal.applied_ontology_version_id
        else None,
    }


async def review_schema_proposal(
    proposal_id: str,
    approved: bool,
    reviewed_by: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Ghi nhận quyết định phê duyệt hoặc từ chối đề xuất lược đồ vào PostgreSQL; không bao giờ tự gọi nếu không có hướng dẫn từ người dùng."""
    del tool_context
    container = await get_service_container()
    proposal = await container.ontology_lifecycle.review_proposal(
        proposal_id, approved=approved, reviewed_by=reviewed_by
    )
    if (
        not approved
        and proposal.source_ingestion_id
        and proposal.source_batch_index is not None
    ):
        try:
            await container.repository.reject_batch_schema_proposal(
                proposal.source_ingestion_id,
                proposal.source_batch_index,
                [
                    {
                        "code": "SCHEMA_PROPOSAL_REJECTED",
                        "message": f"Schema proposal {proposal.id} was rejected",
                        "location": f"batch[{proposal.source_batch_index}]",
                        "retryable": True,
                    }
                ],
            )
        except KeyError:
            pass
    return {
        "success": True,
        "stage": "schema_proposal_reviewed",
        "proposalId": str(proposal.id),
        "proposalStatus": proposal.status.value,
    }


async def apply_schema_proposal(
    proposal_id: str,
    new_version_code: str,
    applied_by: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Áp dụng đề xuất đã APPROVED bằng cách biên dịch và kích hoạt phiên bản ontology mới trong PostgreSQL."""
    del tool_context
    container = await get_service_container()
    version = await container.ontology_lifecycle.apply_proposal(
        proposal_id, new_version_code=new_version_code, applied_by=applied_by
    )
    return {
        "success": True,
        "stage": "schema_proposal_applied",
        "proposalId": proposal_id,
        "ontologyVersionId": str(version.id),
        "ontologyVersion": version.version,
        "nextAction": "rebase_ingestion",
    }


async def rebase_ingestion(
    ingestion_id: str,
    target_ontology_version_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Chuyển đổi workspace ingestion trong RAM sang phiên bản ontology mục tiêu từ PostgreSQL và vô hiệu hóa các batch bị ảnh hưởng."""
    container = await get_service_container()
    active = await container.ontology_cache.active_version()
    if target_ontology_version_id != active.version_id:
        return _tool_error(
            "ontology_rebase",
            "TARGET_ONTOLOGY_NOT_ACTIVE",
            "A job can only be explicitly rebased to the current ACTIVE ontology version.",
        )
    catalog = await container.ontology_cache.list_scopes(target_ontology_version_id)
    hashes = {item.scope_key: item.schema_hash for item in catalog if item.schema_hash}
    current = await operations.required_workspace(container.repository, ingestion_id)
    merged_hashes: dict[str, str] = {}
    for batch in current.batches:
        if batch.scope_keys and all(
            hashes.get(key) == batch.snapshot_hashes.get(key)
            for key in batch.scope_keys
        ):
            projection = await container.ontology_cache.get_many(
                batch.scope_keys, target_ontology_version_id
            )
            merged_hashes["\x1f".join(batch.scope_keys)] = projection.digest
    workspace = await container.repository.rebase_ontology_version(
        ingestion_id, target_ontology_version_id, hashes, merged_hashes
    )
    tool_context.state["active_ingestion_id"] = ingestion_id
    return operations.status_payload(workspace)


async def delete_document(
    document_id: str,
    tool_context: ToolContext,
    if_missing: str = "error",
) -> dict[str, Any]:
    """Vô hiệu hóa một tài liệu nguồn và thu hồi các tri thức phụ thuộc trong Knowledge Graph Neo4j cũng như cập nhật trạng thái trong RAM."""
    del tool_context
    if if_missing not in {"error", "ignore"}:
        return _tool_error("delete", "INVALID_IF_MISSING", "Use 'error' or 'ignore'")
    try:
        container = await get_service_container()
        result = await operations.delete_document(
            container.repository, container.graph_store, document_id, if_missing
        )
        log_ingestion_event(f"DELETE_DOCUMENT [{document_id}]", payload=result)
        return result
    except Exception as exc:
        log_ingestion_event(f"DELETE_DOCUMENT [{document_id}]", error=str(exc))
        raise


async def rollback_document_version(
    document_id: str,
    version_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Thu hồi/quay lui (rollback) phiên bản tài liệu về một phiên bản cũ trước đó trong Knowledge Graph Neo4j và cập nhật trạng thái trong RAM."""
    del tool_context
    try:
        container = await get_service_container()
        result = await operations.rollback_version(
            container.repository, container.graph_store, document_id, version_id
        )
        log_ingestion_event(
            f"ROLLBACK_VERSION [{document_id} -> {version_id}]", payload=result
        )
        return result
    except Exception as exc:
        log_ingestion_event(
            f"ROLLBACK_VERSION [{document_id} -> {version_id}]", error=str(exc)
        )
        raise


# Danh sách toàn bộ các ADK Ingestion Tools được xuất bản cho Agent
INGESTION_TOOLS = (
    begin_ingestion,
    get_ingestion_batch,
    submit_ingestion_batch,
    finalize_ingestion,
    fill_ingestion,
    get_ingestion_status,
    list_ontology_scopes,
    load_ontology_scopes,
    create_schema_proposal,
    get_schema_proposal,
    review_schema_proposal,
    apply_schema_proposal,
    rebase_ingestion,
    delete_document,
    rollback_document_version,
)


def get_ingestion_tools() -> list:
    """Trả về danh sách các tool phục vụ Ingestion để đăng ký vào Agent ADK."""
    return list(INGESTION_TOOLS)


def _tool_error(stage: str, code: str, message: str) -> dict[str, Any]:
    """Hàm phụ trợ định dạng cấu trúc phản hồi lỗi chuẩn tắc cho các tools."""
    return {
        "success": False,
        "stage": stage,
        "terminal": True,
        "ingestionId": None,
        "nextAction": None,
        "errors": [{"code": code, "message": message}],
    }


__all__ = ["INGESTION_TOOLS", "get_ingestion_tools"]
