"""Schemas for document chunks, loaded sources, and ingestion provenance."""

import uuid
from dataclasses import dataclass

from pydantic import Field

from app.schemas.ingestion.base import IngestionModel


class DocumentChunk(IngestionModel):
    """Schema đại diện cho một đoạn văn bản (chunk) được phân tách từ tài liệu nguồn."""

    # Vị trí thứ tự của chunk trong tài liệu (bắt đầu từ 0)
    index: int = Field(ge=0)
    # Tên file nguồn hoặc đường dẫn nguồn của tài liệu (ví dụ: "chinh_sach.md")
    source: str = Field(min_length=1)
    # Tên tiêu đề mục (section heading) gần nhất chứa chunk này (vd: "Quy định đổi trả"), có thể None
    section: str | None = None
    # Nội dung văn bản thuần của chunk sau khi chuẩn hóa và loại bỏ cú pháp thừa
    content: str = Field(min_length=1)
    # ID định danh ổn định và duy nhất của riêng chunk này (bắt đầu bằng "chk_...")
    chunk_id: str = Field(min_length=1, alias="chunkId")
    # Mã băm SHA-256 nội dung của chunk để phát hiện thay đổi hoặc trùng lặp
    content_hash: str = Field(min_length=1, alias="contentHash")
    # Đường dẫn cấu trúc phân cấp cây tiêu đề chứa chunk (vd: "Chương 1 > Mục 1.2#0")
    structural_path: str = Field(min_length=1, alias="structuralPath")
    # Số dòng bắt đầu của chunk trong file nguồn gốc (đánh số từ 1)
    start_line: int = Field(ge=1, alias="startLine")
    # Số dòng kết thúc của chunk trong file nguồn gốc (đánh số từ 1)
    end_line: int = Field(ge=1, alias="endLine")


@dataclass(frozen=True)
class LoadedDocument:
    """Đối tượng bất biến (immutable) lưu trữ toàn bộ nội dung tài liệu sau khi được nạp vào bộ nhớ."""

    # Tên file hoặc nguồn của tài liệu
    source: str
    # Phần mở rộng / đuôi file (ví dụ: ".md", ".txt")
    suffix: str
    # Toàn bộ nội dung văn bản UTF-8 của tài liệu
    text: str
    # Kiểu MIME của tài liệu (ví dụ: "text/markdown", "text/plain"), mặc định là None
    mime_type: str | None = None


@dataclass(frozen=True)
class IngestionDocumentData:
    """Dữ liệu tài liệu ingestion lưu trữ trong in-memory repository."""

    id: uuid.UUID
    document_key: str
    name: str
    current_version_id: uuid.UUID | None


@dataclass(frozen=True)
class IngestionDocumentVersionData:
    """Dữ liệu phiên bản tài liệu ingestion lưu trữ trong in-memory repository."""

    id: uuid.UUID
    document_id: uuid.UUID
    content_hash: str
    ontology_version_id: uuid.UUID
    ontology_digest: str
    status: str


@dataclass(frozen=True)
class IngestionChunkData:
    """Dữ liệu chunk tài liệu lưu trữ trong in-memory repository."""

    id: uuid.UUID
    document_version_id: uuid.UUID
    chunk_id: str
    chunk_index: int
    text: str
    content_hash: str
    token_count: int
    section: str | None
    page_start: int | None
    page_end: int | None
    source_anchor: str


__all__ = [
    "DocumentChunk",
    "IngestionChunkData",
    "IngestionDocumentData",
    "IngestionDocumentVersionData",
    "LoadedDocument",
]
