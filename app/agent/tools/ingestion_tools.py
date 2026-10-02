"""Các công cụ (ADK Function Tools) phục vụ quy trình nạp và trích xuất tri thức Taekwondo bền vững.

Mỗi tool ánh xạ tới đúng một primitive deterministic. Quy trình semantic và thứ tự gọi
tool thuộc quyền sở hữu của ingestion SKILL.md.
"""

from typing import Any

from google.adk.tools import ToolContext

from app.core.ingestion_runtime import get_service_container
from app.services.ingestion import operations
from app.utils.ingestion_logger import log_ingestion_event


async def begin_ingestion(
    artifact_name: str,
    tool_context: ToolContext,
    document_key: str | None = None,
    scope_hint: str | None = None,
) -> dict[str, Any]:
    """Khởi tạo hoặc khôi phục quy trình nạp tài liệu (PDF, DOCX, Markdown, TXT) theo batch.

    Công dụng:
        - Tải file nhị phân của artifact từ phiên làm việc ADK (ToolContext).
        - Parse tài liệu, làm sạch, băm nhỏ thành chunks và nhóm thành batches.
        - Khởi tạo hoặc tìm lại IngestionJob tương ứng trong PostgreSQL.
        - Lưu `ingestionId` vào trạng thái phiên (`tool_context.state`) để duy trì ngữ cảnh.

    Tham số:
        artifact_name: Tên của artifact/tài liệu được tải lên trong session.
        tool_context: Ngữ cảnh thực thi tool của ADK (chứa state và artifact loader).
        document_key: Khóa định danh tài liệu duy nhất (tùy chọn; mặc định sinh từ tên file).
        scope_hint: Gợi ý phạm vi ontology liên quan (VD: 'core', 'course', 'finance'...).

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
        result = await operations.get_batch(container.repository, ingestion_id, batch_index)
        log_ingestion_event(f"GET_BATCH [idx={batch_index}]", payload=result)
        return result
    except Exception as exc:
        log_ingestion_event(f"GET_BATCH [idx={batch_index}]", error=str(exc))
        raise


async def submit_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    scope_key: str,
    graph_fragment: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Xác thực và lưu tạm (stage) một mảnh đồ thị tri thức (GraphPatchFragment) cho batch."""
    try:
        container = await get_service_container()
        result = await operations.submit_batch(
            container.repository,
            container.graph_store,
            container.ontology_cache,
            ingestion_id,
            batch_index,
            scope_key,
            graph_fragment,
        )

        tool_context.state["active_ingestion_id"] = ingestion_id
        tool_context.state["ingestion_checkpoint"] = {
            key: result.get(key)
            for key in ("stage", "nextAction", "nextBatch")
            if result.get(key) is not None
        }
        log_ingestion_event(f"SUBMIT_BATCH [idx={batch_index}, scope={scope_key}]", payload=result)
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
    """Ghi chính thức tri thức đã hoàn tất nạp vào Neo4j và thực hiện đọc kiểm chứng (read-back)."""
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


async def load_ontology_scope(
    scope_key: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Tải định nghĩa Schema Ontology rút gọn tương ứng với phạm vi tri thức cần xử lý."""
    container = await get_service_container()
    result = await operations.load_scope(container.ontology_cache, scope_key)
    tool_context.state["active_ontology_scope"] = scope_key
    return result


async def validate_graph_patch(
    graph_patch: dict[str, Any],
    tool_context: ToolContext,
    scope_key: str = "core",
) -> dict[str, Any]:
    """Kiểm tra tính hợp lệ của một mảnh đồ thị với Ontology mà không lưu vào cơ sở dữ liệu."""
    del tool_context
    container = await get_service_container()
    return await operations.validate_patch(
        container.ontology_cache, graph_patch, scope_key
    )


async def fill_graph_patch(
    graph_patch: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Chặn các yêu cầu ghi dữ liệu trực tiếp vào đồ thị mà không có tài liệu nguồn chứng minh."""
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
    """Vô hiệu hóa một tài liệu nguồn và thu hồi các tri thức phụ thuộc trong Knowledge Graph."""
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
    """Khôi phục phiên bản tài liệu về một phiên bản cũ trước đó dựa trên lịch sử truy vết."""
    del tool_context
    try:
        container = await get_service_container()
        result = await operations.rollback_version(
            container.repository, container.graph_store, document_id, version_id
        )
        log_ingestion_event(f"ROLLBACK_VERSION [{document_id} -> {version_id}]", payload=result)
        return result
    except Exception as exc:
        log_ingestion_event(f"ROLLBACK_VERSION [{document_id} -> {version_id}]", error=str(exc))
        raise


# Danh sách toàn bộ các ADK Ingestion Tools được xuất bản cho Agent
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
