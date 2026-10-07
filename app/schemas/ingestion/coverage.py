"""Chunking, coverage auditing, and validation issues."""

from typing import Literal

from pydantic import Field

from app.schemas.ingestion.base import IngestionModel

CoverageDecision = Literal[
    "MAPPED",                   # Đoạn văn đã được trích xuất thành nodes/edges đầy đủ
    "NOT_RELEVANT",            # Đoạn văn không chứa thông tin liên quan đến phạm vi chuyên môn
    "NO_RELEVANT_FACT",        # Đoạn văn có đề cập chủ đề nhưng không có sự kiện/thuộc tính cụ thể nào
    "DUPLICATE_EVIDENCE",      # Thông tin bị trùng lặp với bằng chứng đã xử lý ở chunk trước
    "UNSUPPORTED_BY_ONTOLOGY", # Thông tin có thật nhưng Ontology hiện tại chưa hỗ trợ mô hình hóa
    "AMBIGUOUS",               # Thông tin mập mờ, mâu thuẫn, không đủ căn cứ để trích xuất an toàn
    "FAILED",                  # Trích xuất thất bại do lỗi xử lý
    "SCHEMA_GAP",              # Phát hiện khoảng trống schema cần cập nhật ontology trong tương lai
]


class ChunkCoverage(IngestionModel):
    """Báo cáo đánh giá mức độ bao phủ và xử lý đối với từng chunk văn bản.

    Yêu cầu LLM giải trình rõ vì sao một chunk được trích xuất hoặc bỏ qua,
    giúp kiểm toán chất lượng và hạn chế tối đa việc bỏ sót tri thức.
    """

    chunk_index: int = Field(
        ge=0,
        description="Chỉ số index của DocumentChunk được đánh giá.",
    )
    decision: CoverageDecision = Field(
        description="Quyết định xử lý đối với chunk văn bản (MAPPED, NOT_RELEVANT, SCHEMA_GAP,...).",
    )
    reason: str = Field(
        min_length=1,
        description="Giải thích lý do cụ thể cho quyết định trên.",
    )


class ValidationIssue(IngestionModel):
    """Mô tả một lỗi hoặc cảnh báo phát sinh khi kiểm tra tính hợp lệ của đồ thị."""

    code: str = Field(
        description="Mã định danh lỗi (ví dụ: 'MISSING_EVIDENCE', 'INVALID_ONTOLOGY_CLASS', 'DISCONNECTED_NODE').",
    )
    message: str = Field(
        description="Thông báo chi tiết giải thích nguyên nhân lỗi.",
    )
    location: str | None = Field(
        default=None,
        description="Vị trí phát sinh lỗi (node ID, edge name, property hoặc chunk index).",
    )
    retryable: bool = Field(
        default=False,
        description="Cho biết lỗi này có thể khắc phục được bằng cách yêu cầu LLM trích xuất lại hay không.",
    )


class PreparedChunk(IngestionModel):
    """Dữ liệu một đoạn văn bản (chunk) đã được chuẩn bị đầy đủ để nạp vào prompt của LLM."""

    chunk_id: str = Field(
        description="Định danh duy nhất của chunk (ví dụ: 'chk_taekwondo_001').",
    )
    chunk_index: int = Field(
        ge=0,
        description="Thứ tự xuất hiện của chunk trong tài liệu (bắt đầu từ 0).",
    )
    text: str = Field(
        description="Nội dung văn bản thuần của chunk.",
    )
    content_hash: str = Field(
        description="Mã băm SHA-256 nội dung của chunk để phát hiện thay đổi.",
    )
    token_count: int = Field(
        ge=0,
        description="Số lượng token ước tính của chunk.",
    )
    section: str | None = Field(
        default=None,
        description="Tiêu đề mục chứa chunk này (nếu có).",
    )
    page_start: int | None = Field(
        default=None,
        ge=1,
        description="Trang bắt đầu trong tài liệu gốc.",
    )
    page_end: int | None = Field(
        default=None,
        ge=1,
        description="Trang kết thúc trong tài liệu gốc.",
    )
    source_anchor: str = Field(
        description="Đoạn neo định vị tham chiếu nguồn (ví dụ: 'tai_lieu.pdf#page=3').",
    )
