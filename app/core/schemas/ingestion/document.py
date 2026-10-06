"""Schemas for document chunks, loaded sources, and ingestion provenance."""

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class IngestionBaseModel(BaseModel):
    """Model cơ sở cho các schema ingestion với cấu hình tự động camelCase và chuẩn hóa dữ liệu."""
    model_config = ConfigDict(
        # Tự động chuyển đổi tên thuộc tính snake_case sang camelCase (vd: chunk_id -> chunkId) khi serialize JSON
        alias_generator=lambda name: "".join(
            word if index == 0 else word.capitalize()
            for index, word in enumerate(name.split("_"))
        ),
        populate_by_name=True,      # Cho phép gán giá trị bằng cả tên gốc (snake_case) lẫn alias (camelCase)
        extra="ignore",             # Bỏ qua các trường thừa không được định nghĩa trong schema
        str_strip_whitespace=True,  # Tự động loại bỏ khoảng trắng thừa ở 2 đầu chuỗi
    )


class DocumentChunk(IngestionBaseModel):
    """Schema đại diện cho một đoạn văn bản (chunk) được phân tách từ tài liệu nguồn."""
    # Vị trí thứ tự của chunk trong tài liệu (bắt đầu từ 0)
    index: int = Field(ge=0)
    # Tên file nguồn hoặc đường dẫn nguồn của tài liệu (ví dụ: "chinh_sach.md")
    source: str = Field(min_length=1)
    # Tên tiêu đề mục (section heading) gần nhất chứa chunk này (vd: "Quy định đổi trả"), có thể None
    section: str | None = None
    # Nội dung văn bản thuần của chunk sau khi chuẩn hóa và loại bỏ cú pháp thừa
    content: str = Field(min_length=1)
    # ID định danh ổn định của tài liệu nguồn (bắt đầu bằng "doc_...")
    document_id: str = Field(min_length=1, alias="documentId")
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


__all__ = [
    "DocumentChunk",
    "IngestionBaseModel",
    "LoadedDocument",
]

