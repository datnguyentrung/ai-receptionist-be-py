"""Tập hợp các hàm tiện ích (Helper Functions) cho Ingestion và Workspace.

Module này cung cấp các hàm hỗ trợ tính toán mã băm, chuẩn hóa UUID, trích xuất chunk,
tính toán fingerprint xác thực đồ thị và lập chỉ mục thực thể cho quy trình Ingestion.

Danh sách các hàm trong module:
- `to_uuid`: Chuẩn hóa chuỗi hoặc UUID về đối tượng uuid.UUID.
- `dict_digest`: Tạo mã băm SHA-256 từ dictionary JSON đã sắp xếp khóa tất định.
- `workspace_chunks`: Lấy danh sách PreparedChunk tương ứng với danh sách chunk_indexes.
- `workspace_fingerprint`: Tạo mã vân tay xác thực toàn vẹn cho các batch đã STAGED.
- `stable_entity_key`: Tạo khóa băm định danh duy nhất cho thực thể theo class_name và identity.
- `staged_entity_index`: Tạo bảng chỉ mục các thực thể đã staged phục vụ đối soát liên batch.
- `snapshot_bindings_unchanged`: Kiểm tra tính bất biến của hash snapshot scope bindings.
"""

import hashlib
import json
import uuid
from typing import Any

from app.schemas import PreparedChunk, Workspace


# ------------------------------------------------------------------------------
# Hàm tiện ích chuyển đổi giá trị chuỗi hoặc UUID về kiểu uuid.UUID chuẩn.
#
# Tác dụng:
#   Giúp chuẩn hóa dữ liệu đầu vào (chuỗi UUID hoặc UUID object) thành đối tượng uuid.UUID hợp lệ.
#
# Đầu vào (Input):
#   - value (str | uuid.UUID): Giá trị đầu vào cần chuẩn hóa.
#
# Đầu ra (Output):
#   - uuid.UUID: Đối tượng UUID hợp lệ.
# ------------------------------------------------------------------------------
def to_uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


# ------------------------------------------------------------------------------
# Hàm tiện ích tạo mã băm SHA-256 (hex string) từ dictionary dữ liệu.
#
# Tác dụng:
#   Dictionary được sắp xếp khóa (sort_keys=True) và mã hóa UTF-8 để đảm bảo tính tất định.
#
# Đầu vào (Input):
#   - data (dict): Dữ liệu cần băm.
#
# Đầu ra (Output):
#   - str: Chuỗi mã băm SHA-256 dạng hex.
# ------------------------------------------------------------------------------
def dict_digest(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


# ------------------------------------------------------------------------------
# Trích xuất danh sách PreparedChunk từ workspace dựa theo danh sách chỉ số chunk_indexes.
#
# Tác dụng:
#   Lọc ra các đoạn văn bản (chunks) thuộc batch cụ thể và đóng gói thành PreparedChunk
#   phục vụ nạp vào prompt cho LLM.
#
# Đầu vào (Input):
#   - workspace (Workspace): Đối tượng workspace chứa dữ liệu các chunk.
#   - chunk_indexes (list[int]): Danh sách các chỉ số index cần lấy.
#
# Đầu ra (Output):
#   - list[PreparedChunk]: Danh sách các PreparedChunk tương ứng sẵn sàng nạp vào prompt LLM.
# ------------------------------------------------------------------------------
def workspace_chunks(
    workspace: Workspace, chunk_indexes: list[int]
) -> list[PreparedChunk]:
    indexes = set(chunk_indexes)
    return [
        PreparedChunk(
            chunk_id=item.chunk_id,
            chunk_index=item.chunk_index,
            text=item.text,
            content_hash=item.content_hash,
            token_count=item.token_count,
            section=item.section,
            page_start=item.page_start,
            page_end=item.page_end,
            source_anchor=item.source_anchor,
        )
        for item in workspace.chunks
        if item.chunk_index in indexes
    ]


# ------------------------------------------------------------------------------
# Tính toán mã vân tay (Fingerprint) xác thực tính toàn vẹn của đồ thị patch trong workspace.
#
# Tác dụng:
#   Mã băm kết hợp ID phiên bản, ID ontology, mã băm nội dung tài liệu và toàn bộ fragment
#   của các batch đã STAGED thành công.
#
# Đầu vào (Input):
#   - workspace (Workspace): Đối tượng workspace cần tạo fingerprint.
#
# Đầu ra (Output):
#   - str: Chuỗi SHA-256 fingerprint đại diện cho trạng thái đồ thị hiện tại của workspace.
# ------------------------------------------------------------------------------
def workspace_fingerprint(workspace: Workspace) -> str:
    return dict_digest(
        {
            "versionId": str(workspace.version.id),
            "ontologyVersionId": str(workspace.job.ontology_version_id),
            "contentHash": workspace.version.content_hash,
            "staged": [
                {
                    "batchIndex": item.batch_index,
                    "scopeKeys": item.scope_keys,
                    "mergedSchemaHash": item.merged_schema_hash,
                    "fragment": item.graph_fragment,
                }
                for item in workspace.batches
                if item.status == "STAGED"
            ],
        }
    )


# ------------------------------------------------------------------------------
# Tạo mã khóa định danh ổn định (Stable Entity Key) cho một thực thể đồ thị.
#
# Tác dụng:
#   Kết hợp tên lớp thực thể (class_name) cùng tập thuộc tính định danh duy nhất (identity),
#   sau đó tính mã hash SHA-256 tất định.
#
# Đầu vào (Input):
#   - class_name (str): Tên lớp/loại thực thể theo Ontology (ví dụ: 'TheThucQuyen').
#   - identity (dict[str, Any]): Tập thuộc tính định danh duy nhất của thực thể.
#
# Đầu ra (Output):
#   - str: Chuỗi SHA-256 key đại diện cho thực thể.
# ------------------------------------------------------------------------------
def stable_entity_key(class_name: str, identity: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {"class": class_name, "identity": identity},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


# ------------------------------------------------------------------------------
# Lập bảng chỉ mục (Entity Index) của tất cả thực thể đã được STAGED trong workspace.
#
# Tác dụng:
#   Hỗ trợ đối soát và tham chiếu thực thể chéo giữa các batch kế tiếp nhau,
#   giúp LLM nhận diện các thực thể đã xuất hiện ở các batch trước đó để liên kết chính xác.
#
# Đầu vào (Input):
#   - workspace (Workspace): Đối tượng workspace cần lập chỉ mục.
#   - before_batch (int | None): Giới hạn chỉ lập chỉ mục các batch trước chỉ số này (nếu None sẽ lập toàn bộ).
#
# Đầu ra (Output):
#   - dict[str, dict[str, Any]]: Bản đồ từ 'entity:<stableKey>' tới thông tin chi tiết thực thể.
# ------------------------------------------------------------------------------
def staged_entity_index(
    workspace: Workspace, before_batch: int | None = None
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for batch in workspace.batches:
        if before_batch is not None and batch.batch_index >= before_batch:
            continue
        if batch.status != "STAGED" or not batch.graph_fragment:
            continue
        for node in batch.graph_fragment.get("nodes", []):
            identity = node.get("identity") or {}
            key = stable_entity_key(node["className"], identity)
            display_properties = {
                prop.get("propertyName"): prop.get("value")
                for prop in node.get("properties", [])
                if prop.get("value") not in (None, "", [], {})
            }
            result[f"entity:{key}"] = {
                "ref": f"entity:{key}",
                "stableKey": key,
                "className": node["className"],
                "identity": identity,
                "displayProperties": {
                    **identity,
                    **display_properties,
                },
            }
    return result


# ------------------------------------------------------------------------------
# Kiểm tra xem mã băm snapshot của các scope bindings có giữ nguyên so với bản đồ hash mục tiêu hay không.
#
# Tác dụng:
#   Dùng để phát hiện xem ontology scope của batch có bị thay đổi hay không khi xem xét tái sử dụng kết quả.
#
# Đầu vào (Input):
#   - bindings (list[Any]): Danh sách các đối tượng binding chứa scope_key và snapshot_hash.
#   - target_hashes (dict[str, str]): Bản đồ scope_key -> snapshot_hash mới nhất.
#
# Đầu ra (Output):
#   - bool: True nếu tất cả scope bindings đều khớp hash với target_hashes, ngược lại False.
# ------------------------------------------------------------------------------
def snapshot_bindings_unchanged(
    bindings: list[Any], target_hashes: dict[str, str]
) -> bool:
    return bool(bindings) and all(
        target_hashes.get(item.scope_key) == item.snapshot_hash for item in bindings
    )


__all__ = [
    "dict_digest",
    "snapshot_bindings_unchanged",
    "stable_entity_key",
    "staged_entity_index",
    "to_uuid",
    "workspace_chunks",
    "workspace_fingerprint",
]
