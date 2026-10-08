"""Chính sách điều phối quy trình (Workflow Policy) cho tiến trình Ingestion.

Mô tả:
    Module này đóng vai trò là nguồn chân lý tất định (deterministic source of truth)
    quản lý máy trạng thái (state machine) và quyền thực thi các hành động/công cụ.
    Nó ngăn chặn LLM gọi các công cụ không hợp lệ tại từng giai đoạn xử lý ingestion
    (ví dụ: không thể nạp batch khi đang chờ duyệt schema, không thể submit khi job đã thất bại/hoàn thành).

Danh sách các cấu trúc dữ liệu và hàm trong module:
    - WorkflowDecision: Data class đại diện cho kết quả đánh giá quyền thực thi hành động.
    - normalize_status(status): Chuẩn hóa giá trị trạng thái từ Enum hoặc chuỗi về dạng string.
    - evaluate_workflow(status, action): Đánh giá tính hợp lệ của hành động đối với trạng thái hiện tại.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowDecision:
    """Đại diện cho quyết định điều phối quy trình (Workflow Decision).

    Attributes:
        allowed (bool): Hành động có được phép thực thi trong trạng thái hiện tại hay không.
        terminal (bool): Trạng thái hiện tại có phải là trạng thái kết thúc (terminal) hay không.
        next_action (str | None): Hành động tiếp theo được đề xuất nếu đã đến trạng thái kết thúc (hoặc None).
    """
    allowed: bool
    terminal: bool
    next_action: str | None


# Bản đồ ánh xạ danh sách các hành động/công cụ được phép thực thi tương ứng với từng trạng thái job
WORKFLOW_POLICY: dict[str, frozenset[str]] = {
    # Giai đoạn đang chia batch và trích xuất dữ liệu
    "BATCHING": frozenset({
        "get_batch",
        "list_scopes",
        "load_scopes",
        "submit_batch",
        "finalize",
        "create_schema_proposal",
        "get_status",
    }),
    # Giai đoạn bị chặn do phát hiện schema ontology mới cần duyệt
    "BLOCKED_SCHEMA": frozenset({
        "get_status",
        "get_schema_proposal",
        "review_schema_proposal",
        "apply_schema_proposal",
        "rebase_ingestion",
    }),
    # Giai đoạn trích xuất xong và sẵn sàng commit dữ liệu vào KG
    "READY": frozenset({"get_status", "fill"}),
    # Giai đoạn đã commit hoàn tất vào Knowledge Graph
    "COMMITTED": frozenset({"get_status", "fill"}),
    # Giai đoạn xử lý thất bại
    "FAILED": frozenset({"get_status"}),
}

# Ánh xạ các trạng thái kết thúc (terminal states) và hành động tương ứng tiếp theo
TERMINAL_NEXT_ACTION: dict[str, str | None] = {
    "FAILED": "explicit_extraction_failure",
    "COMMITTED": None,
    "DELETED": None,
    "ROLLED_BACK": None,
}


def normalize_status(status: Any) -> str:
    """Chuẩn hóa giá trị trạng thái thành chuỗi văn bản.

    Args:
        status (Any): Đối tượng trạng thái (chuỗi, Enum instance, hoặc kiểu dữ liệu bất kỳ).

    Returns:
        str: Chuỗi đại diện cho trạng thái chuẩn hóa.
    """
    # 1. Trích xuất thuộc tính .value nếu status là Enum instance, ngược lại giữ nguyên
    value = getattr(status, "value", status)
    # 2. Ép kiểu về string
    return str(value)


def evaluate_workflow(status: Any, action: str) -> WorkflowDecision:
    """Đánh giá tính hợp lệ của một hành động dựa trên trạng thái hiện tại của job.

    Args:
        status (Any): Trạng thái hiện tại của Job Ingestion.
        action (str): Tên hành động hoặc tên công cụ agent dự định thực thi.

    Returns:
        WorkflowDecision: Quyết định cho phép thực thi, trạng thái kết thúc và gợi ý bước tiếp theo.
    """
    # 1. Chuẩn hóa chuỗi trạng thái
    status_key = normalize_status(status)
    # 2. Lấy danh sách hành động hợp lệ cho trạng thái này (mặc định chỉ cho phép get_status nếu trạng thái lạ)
    allowed_actions = WORKFLOW_POLICY.get(status_key, frozenset({"get_status"}))
    # 3. Kiểm tra xem trạng thái này có phải trạng thái kết thúc (terminal) hay không
    terminal = status_key in TERMINAL_NEXT_ACTION
    # 4. Trả về quyết định điều phối hoàn chỉnh
    return WorkflowDecision(
        allowed=action in allowed_actions,
        terminal=terminal,
        next_action=TERMINAL_NEXT_ACTION.get(status_key),
    )


__all__ = [
    "TERMINAL_NEXT_ACTION",
    "WORKFLOW_POLICY",
    "WorkflowDecision",
    "evaluate_workflow",
    "normalize_status",
]
