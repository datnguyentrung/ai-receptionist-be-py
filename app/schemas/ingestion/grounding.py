"""Grounding and provenance schemas for evidence-backed fact extraction."""

from typing import Any

from pydantic import Field

from app.schemas.ingestion.base import IngestionModel


class Evidence(IngestionModel):
    """Bằng chứng trích dẫn văn bản nguồn phục vụ kiểm chứng thực tế (Grounding / Provenance).

    Đảm bảo mọi Node, Edge hoặc Property được trích xuất đều có thể truy vết chính xác
    về vị trí đoạn văn bản gốc trong tài liệu, ngăn chặn hiện tượng ảo giác (hallucination).
    """

    source: str = Field(
        default="document.md",
        description="Tên tệp, URI hoặc định danh của tài liệu nguồn chứa bằng chứng.",
    )
    chunk_index: int = Field(
        ge=0,
        description="Chỉ số index của DocumentChunk chứa đoạn văn bản bằng chứng.",
    )
    text: str = Field(
        default="",
        description="Đoạn văn bản trích dẫn nguyên văn từ tài liệu nguồn làm căn cứ chứng minh.",
    )
    section: str | None = Field(
        default=None,
        description="Tiêu đề mục hoặc phân đoạn chứa đoạn trích (nếu có).",
    )
    page: int | None = Field(
        default=None,
        ge=1,
        description="Số trang trong tài liệu gốc (dành cho file PDF/sách phân trang).",
    )


class PropertyFact(IngestionModel):
    """Thuộc tính đơn lẻ của thực thể kèm theo bằng chứng văn bản trực tiếp.

    Mỗi thuộc tính khi trích xuất bắt buộc phải gắn liền với ít nhất 1 bằng chứng (Evidence)
    để phục vụ khâu kiểm định Ontology sau này.
    """

    property_name: str = Field(
        min_length=1,
        description="Tên thuộc tính chuẩn hóa theo định nghĩa của Ontology (ví dụ: 'ten_the_thuc', 'so_luong_don').",
    )
    value: Any = Field(
        description="Giá trị của thuộc tính (chuỗi, số, boolean, danh sách,...).",
    )
    evidence: list[Evidence] = Field(
        min_length=1,
        description="Danh sách các bằng chứng trích dẫn chứng minh cho giá trị thuộc tính này.",
    )
