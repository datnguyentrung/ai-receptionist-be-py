"""Workspace and batch schemas for ingestion session management.

Module này định nghĩa các mô hình dữ liệu (Pydantic models và Dataclasses) phục vụ
cho quy trình Staged Ingestion (Phase 2b) và Ingestion Workspace Store:
- `IngestionProvenance`: Quản lý nguồn gốc trích xuất (phiên bản ontology, skill digest, model).
- `IngestionBatch`: Đại diện cho một nhóm các chunk tài liệu được gom lại để xử lý trích xuất theo đợt.
- `IngestionWorkspace`: Không gian làm việc lưu trữ toàn bộ trạng thái phiên ingestion của một tài liệu.
- `IngestionJobData`: Dữ liệu job ingestion trong bộ nhớ (in-memory repository).
- `IngestionBatchData`: Dữ liệu batch trích xuất trong bộ nhớ.
- `Workspace`: Cấu trúc workspace tổng hợp trong bộ nhớ.
"""

import uuid
from dataclasses import dataclass, field

from pydantic import Field

from app.schemas.ingestion.base import IngestionModel
from app.schemas.ingestion.document import (
    DocumentChunk,
    IngestionChunkData,
    IngestionDocumentData,
    IngestionDocumentVersionData,
)
from app.schemas.ingestion.graph_patch import GraphPatchFragment


class IngestionProvenance(IngestionModel):
    """Thông tin nguồn gốc và phiên bản phục vụ trích xuất tri thức (Provenance).

    Đảm bảo tính tái lập (reproducibility) và phát hiện thay đổi cấu hình (ontology,
    skill prompt, LLM model) giữa các lần chạy ingestion.
    """

    ontology_version: str = Field(
        alias="ontologyVersion",
        description="Phiên bản ontology schema áp dụng cho quá trình trích xuất.",
    )
    skill_digest: str = Field(
        default="skill-digest-v1",
        alias="skillDigest",
        description="Mã băm/định danh phiên bản của prompt/skill trích xuất tri thức.",
    )
    model_id: str = Field(
        default="default-model",
        alias="modelId",
        description="Định danh mô hình LLM được sử dụng để thực hiện trích xuất.",
    )

    def identity_material(self, artifact_name: str) -> str:
        """Tạo chuỗi dữ liệu gốc (identity material) dùng để băm tạo `ingestion_id`.

        Chuỗi kết hợp tên tài liệu nguồn cùng các thông số nguồn gốc (provenance),
        ngăn cách bởi ký tự null ('\\0') để tránh xung đột chuỗi.

        Args:
            artifact_name: Tên hoặc đường dẫn định danh của tài liệu nguồn.

        Returns:
            Chuỗi raw material sẵn sàng để băm (ví dụ SHA-256).
        """
        return (
            f"{artifact_name}\0{self.ontology_version}\0"
            f"{self.skill_digest}\0{self.model_id}"
        )


class IngestionBatch(IngestionModel):
    """Mô hình đại diện cho một đợt (batch) xử lý ingestion.

    Tài liệu dài được chia thành nhiều batch chứa các chunk liên tiếp nhau
    để vừa với ngữ cảnh (context window) và giới hạn token của LLM.
    """

    index: int = Field(
        ge=0,
        description="Chỉ số thứ tự của batch trong workspace (bắt đầu từ 0).",
    )
    chunk_indexes: list[int] = Field(
        default_factory=list,
        alias="chunkIndexes",
        description="Danh sách index của các DocumentChunk thuộc về batch này.",
    )
    content_chars: int = Field(
        default=0,
        alias="contentChars",
        description="Tổng số ký tự nội dung của toàn bộ chunk trong batch (dùng ước lượng token).",
    )
    status: str = Field(
        default="PENDING",
        description="Trạng thái xử lý của batch (ví dụ: 'PENDING', 'PROCESSING', 'STAGED', 'FAILED').",
    )
    fragment: GraphPatchFragment | None = Field(
        default=None,
        description="Mảnh đồ thị tri thức (GraphPatchFragment) trích xuất được từ batch này sau khi LLM xử lý.",
    )
    scope_keys: list[str] = Field(
        default_factory=list,
        alias="scopeKeys",
        description="Danh sách khóa phạm vi (scope keys) hợp lệ mà batch này được phép trích xuất/tham chiếu.",
    )


class IngestionWorkspace(IngestionModel):
    """Không gian làm việc (Workspace) phiên ingestion cho một tài liệu.

    Lưu trữ toàn bộ trạng thái vòng đời của một phiên trích xuất tri thức:
    từ danh sách chunk, phân chia batch, kết quả fragment từng batch cho đến
    mã vân tay (fingerprint) xác thực đồ thị tổng thể.
    """

    ingestion_id: str = Field(
        min_length=1,
        alias="ingestionId",
        description="Mã định danh duy nhất của phiên ingestion (thường là SHA-256 hash từ identity material).",
    )
    artifact_name: str = Field(
        min_length=1,
        alias="artifactName",
        description="Tên hoặc đường dẫn định danh của tài liệu nguồn đang được xử lý.",
    )
    provenance: IngestionProvenance = Field(
        description="Thông tin nguồn gốc, phiên bản ontology, skill digest và mô hình thực hiện.",
    )
    chunks: list[DocumentChunk] = Field(
        description="Toàn bộ danh sách các chunk đã được phân tách từ tài liệu nguồn.",
    )
    batches: list[IngestionBatch] = Field(
        description="Danh sách các batch được phân bổ để trích xuất tuần tự hoặc song song.",
    )
    validated_fingerprint: str | None = Field(
        default=None,
        alias="validatedFingerprint",
        description="Mã vân tay (fingerprint) xác thực tính toàn vẹn của đồ thị patch sau khi merge và validate.",
    )


@dataclass
class IngestionJobData:
    """Dữ liệu job ingestion lưu trữ trong in-memory repository."""

    id: uuid.UUID
    document_version_id: uuid.UUID
    ontology_version_id: uuid.UUID
    status: str
    stage: str
    scope_hint: str | None
    readiness_fingerprint: str | None
    error_stage: str | None
    error_message: str | None
    summary: dict = field(default_factory=dict)


@dataclass
class IngestionBatchData:
    """Dữ liệu batch trích xuất lưu trữ trong in-memory repository."""

    id: uuid.UUID
    job_id: uuid.UUID
    batch_index: int
    chunk_indexes: list[int]
    status: str
    semantic_fragment: dict | None
    graph_fragment: dict | None
    validation_issues: list[dict]
    validation_attempts: int
    merged_schema_hash: str | None
    scope_keys: list[str] = field(default_factory=list)
    snapshot_hashes: dict[str, str] = field(default_factory=dict)
    validated_baseline: dict | None = None


@dataclass(frozen=True)
class Workspace:
    """Cấu trúc Workspace tổng hợp lưu trữ trong in-memory repository."""

    document: IngestionDocumentData
    version: IngestionDocumentVersionData
    job: IngestionJobData
    chunks: tuple[IngestionChunkData, ...]
    batches: tuple[IngestionBatchData, ...]


__all__ = [
    "IngestionBatch",
    "IngestionBatchData",
    "IngestionJobData",
    "IngestionProvenance",
    "IngestionWorkspace",
    "Workspace",
]
