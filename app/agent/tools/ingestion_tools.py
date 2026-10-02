"""Các công cụ (ADK Function Tools) phục vụ quy trình nạp và trích xuất tri thức Taekwondo bền vững.

Module này cung cấp các tool giao tiếp trực tiếp giữa mô hình ngôn ngữ (LLM Agent) và hệ thống backend
thông qua `IngestionOrchestrator`. Mọi thao tác đều có tính toàn vẹn dữ liệu, kiểm tra ontology,
lưu tạm có thể phục hồi (resumable) và truy vết nguồn gốc (provenance).
"""

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
    """Khởi tạo hoặc khôi phục quy trình nạp tài liệu (PDF, DOCX, Markdown, TXT) theo batch.

    Công dụng:
        - Tải file nhị phân của artifact từ phiên làm việc ADK (ToolContext).
        - Gửi dữ liệu vào Orchestrator để: parse tài liệu, làm sạch, băm nhỏ thành các chunk (chunks)
          và nhóm thành các batch xử lý.
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
    # 1. Tải dữ liệu artifact từ phiên làm việc ADK hiện tại
    artifact = await tool_context.load_artifact(artifact_name)
    if artifact is None or artifact.inline_data is None or artifact.inline_data.data is None:
        return _tool_error(
            "artifact_load",
            "ARTIFACT_NOT_FOUND",
            f"Artifact is unavailable in this ADK session: {artifact_name}",
        )

    # 2. Gọi Orchestrator xử lý phân tích và tạo phiên Ingestion bền vững trong DB
    result = await get_service_container().orchestrator.begin(
        artifact_name,
        bytes(artifact.inline_data.data),
        document_key=document_key,
        scope_hint=scope_hint,
        mime_type=artifact.inline_data.mime_type,
    )

    # 3. Ghi nhận ID phiên nạp vào ADK State để các bước tiếp theo tự động kế thừa
    if result.get("ingestionId"):
        tool_context.state["active_ingestion_id"] = result["ingestionId"]
    return result


async def get_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Lấy nội dung chi tiết các đoạn văn bản (chunks) và ngữ cảnh đã có của một batch.

    Công dụng:
        - Cung cấp văn bản nguồn nguyên vẹn (verbatim text) của từng chunk trong batch để LLM phân tích.
        - Cung cấp thông tin vị trí (trang, section, anchor) để LLM trích xuất thuộc tính kèm bằng chứng.
        - Kèm theo ngữ cảnh tri thức đã trích xuất từ các batch trước nhằm tránh trùng lặp.

    Tham số:
        ingestion_id: Mã định danh tiến trình nạp (UUID).
        batch_index: Chỉ số của batch cần lấy dữ liệu (0-indexed).
        tool_context: Ngữ cảnh ADK.

    Trả về:
        Dictionary chứa danh sách chunks, thông tin batch, và ngữ cảnh canonical graph hiện tại.
    """
    del tool_context  # Không sử dụng trực tiếp trong hàm đọc này
    # Lấy thông tin batch từ Orchestrator
    return await get_service_container().orchestrator.get_batch(ingestion_id, batch_index)


async def submit_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    graph_fragment: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Xác thực và lưu tạm (stage) một mảnh đồ thị tri thức (GraphPatchFragment) cho batch.

    Công dụng:
        - Nhận kết quả trích xuất gồm Nodes, Edges, Properties và Evidence do LLM tạo ra.
        - Kiểm tra tính hợp lệ với Ontology (tên lớp thực thể, kiểu dữ liệu, quan hệ hợp lệ).
        - Kiểm tra độ phủ (Coverage: mọi chunk đều phải được đánh dấu MAPPED hoặc NOT_RELEVANT).
        - Lưu trạng thái hợp lệ vào PostgreSQL dưới dạng STAGED (chưa ghi vào Neo4j).
        - Nếu có lỗi, trả về danh sách issues chi tiết để LLM tự sửa chữa (self-repair).

    Tham số:
        ingestion_id: Mã định danh tiến trình nạp.
        batch_index: Chỉ số của batch đang gửi kết quả.
        graph_fragment: Cấu trúc GraphPatchFragment chứa các thực thể và mối quan hệ trích xuất.
        tool_context: Ngữ cảnh ADK.

    Trả về:
        Dictionary chứa trạng thái xử lý (`stage`), các cảnh báo, lỗi xác thực và `nextBatch` tiếp theo.
    """
    # 1. Gửi fragment qua Orchestrator để xác thực và lưu tạm vào PostgreSQL
    result = await get_service_container().orchestrator.submit_batch(
        ingestion_id, batch_index, graph_fragment
    )

    # 2. Cập nhật checkpoint tiến độ vào ToolContext State
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
    """Kiểm tra độ phủ toàn bộ tài liệu và tạo chữ ký sẵn sàng (readiness fingerprint).

    Công dụng:
        - Đối chiếu xem toàn bộ các batch của tài liệu đã hoàn thành trích xuất hợp lệ chưa.
        - Kiểm tra tính nhất quán toàn cục (Cross-batch integrity).
        - Nếu còn thiếu sót, trả về `repair_required` kèm danh sách `repairBatchIndexes`.
        - Nếu thành công, chuyển stage sang `ready_to_fill` để sẵn sàng ghi vào cơ sở dữ liệu đồ thị.

    Tham số:
        ingestion_id: Mã định danh tiến trình nạp.
        tool_context: Ngữ cảnh ADK.

    Trả về:
        Dictionary chứa trạng thái finalize (`ready_to_fill` hoặc `repair_required`) và fingerprint.
    """
    # Thực hiện kiểm tra toàn vẹn toàn bộ tài liệu qua Orchestrator
    result = await get_service_container().orchestrator.finalize(ingestion_id)
    # Cập nhật checkpoint trạng thái hoàn tất / sửa lỗi
    tool_context.state["ingestion_checkpoint"] = {
        "stage": result.get("stage"),
        "repairBatchIndexes": result.get("repairBatchIndexes", []),
    }
    return result


async def fill_ingestion(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Ghi chính thức tri thức đã hoàn tất nạp vào Neo4j và thực hiện đọc kiểm chứng (read-back).

    Công dụng:
        - Chuyển toàn bộ các fragments từ trạng thái STAGED trong PostgreSQL sang đồ thị Neo4j.
        - Cập nhật phiên bản tài liệu thành ACTIVE trong PostgreSQL.
        - Tự động chạy truy vấn Cypher đọc ngược lại để kiểm chứng số lượng Node/Edge thực tế đã tạo.
        - CHÚ Ý: Chỉ được gọi khi người dùng có yêu cầu lưu/ghi dữ liệu tường minh.

    Tham số:
        ingestion_id: Mã định danh tiến trình nạp đã ở trạng thái `ready_to_fill`.
        tool_context: Ngữ cảnh ADK.

    Trả về:
        Dictionary chứa kết quả commit: số node/edge đã tạo, trạng thái read-back và ID tài liệu.
    """
    # Đẩy dữ liệu vào Neo4j và xác minh qua Orchestrator
    result = await get_service_container().orchestrator.fill(ingestion_id)
    # Lưu trạng thái kết thúc vào context
    tool_context.state["ingestion_checkpoint"] = {"stage": result.get("stage")}
    return result


async def get_ingestion_status(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Truy vấn tiến độ và trạng thái hiện tại của một tiến trình nạp tài liệu.

    Công dụng:
        - Đọc trực tiếp từ cơ sở dữ liệu PostgreSQL nên hoạt động ổn định kể cả khi server vừa khởi động lại.
        - Hỗ trợ khôi phục tiến trình nạp dang dở.

    Tham số:
        ingestion_id: Mã định danh tiến trình nạp.
        tool_context: Ngữ cảnh ADK.
    """
    del tool_context
    return await get_service_container().orchestrator.status(ingestion_id)


async def load_ontology_scope(
    scope_key: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Tải định nghĩa Schema Ontology rút gọn tương ứng với phạm vi tri thức cần xử lý.

    Công dụng:
        - Giúp LLM nắm rõ các lớp thực thể (Entity Types), thuộc tính (Properties), kiểu dữ liệu (Data Types),
          và các mối quan hệ (Relationships) hợp lệ trong ngữ cảnh nhất định.
        - Các scope hợp lệ: 'core', 'course', 'training', 'belt', 'facility', 'finance', 'event'.

    Tham số:
        scope_key: Tên phạm vi ontology cần nạp.
        tool_context: Ngữ cảnh ADK.
    """
    del tool_context
    return await get_service_container().orchestrator.load_scope(scope_key)


async def validate_graph_patch(
    graph_patch: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Kiểm tra tính hợp lệ của một mảnh đồ thị với Ontology mà không lưu vào cơ sở dữ liệu.

    Công dụng:
        - Dùng để kiểm thử (dry-run) xem một cấu trúc patch có vi phạm quy tắc ontology nào không.
    """
    del tool_context
    return await get_service_container().orchestrator.validate_patch(graph_patch)


async def fill_graph_patch(
    graph_patch: dict[str, Any],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Chặn các yêu cầu ghi dữ liệu trực tiếp vào đồ thị mà không có tài liệu nguồn chứng minh.

    Công dụng:
        - Đảm bảo nguyên tắc bảo toàn nguồn gốc (Provenance Guarantee): Mọi tri thức trong Graph
          bắt buộc phải xuất phát từ tài liệu nguồn đã qua quy trình Ingestion.
    """
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
    """Vô hiệu hóa một tài liệu nguồn và thu hồi các tri thức phụ thuộc trong Knowledge Graph.

    Công dụng:
        - Đánh dấu trạng thái tài liệu là DELETED/ARCHIVED trong PostgreSQL.
        - Xóa hoặc cập nhật các node/edge trong Neo4j chỉ dựa vào tài liệu này.

    Tham số:
        document_id: ID hoặc khóa tài liệu cần xóa.
        tool_context: Ngữ cảnh ADK.
        if_missing: Cách xử lý khi không tìm thấy tài liệu ('error' hoặc 'ignore').
    """
    del tool_context
    if if_missing not in {"error", "ignore"}:
        return _tool_error("delete", "INVALID_IF_MISSING", "Use 'error' or 'ignore'")
    return await get_service_container().orchestrator.delete_document(document_id, if_missing)


async def rollback_document_version(
    document_id: str,
    version_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Khôi phục phiên bản tài liệu về một phiên bản cũ trước đó dựa trên lịch sử truy vết.

    Công dụng:
        - Đặt phiên bản chỉ định thành ACTIVE và cập nhật lại dữ liệu đồ thị tương ứng.

    Tham số:
        document_id: ID của tài liệu.
        version_id: ID phiên bản muốn hoàn nguyên.
        tool_context: Ngữ cảnh ADK.
    """
    del tool_context
    return await get_service_container().orchestrator.rollback_version(document_id, version_id)


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

