"""Ontology compiled projections, scopes, and runtime active metadata."""

from pydantic import Field

from app.schemas.ingestion.base import FreeformDict, IngestionModel


class OntologyProjection(IngestionModel):
    """Bản hình chiếu (Compiled Snapshot Projection) của Ontology dùng trong quá trình Ingestion.

    Cung cấp định nghĩa đầy đủ về các thực thể, thuộc tính và mối quan hệ để phục vụ
    tạo prompt trích xuất cho LLM và kiểm tra tính toàn vẹn của đồ thị.
    """

    version_id: str = Field(
        description="ID định danh duy nhất của phiên bản ontology (UUID).",
    )
    version: str = Field(
        description="Chuỗi định danh phiên bản (ví dụ: 'v1.0.0', '2026-10-06').",
    )
    digest: str = Field(
        description="Mã hash tóm lược nội dung toàn bộ snapshot để kiểm tra tính toàn vẹn và cache.",
    )
    scope_key: str = Field(
        description="Khóa định danh phạm vi chuyên môn chính (ví dụ: 'taekwondo_core', 'poomsae').",
    )
    scope_keys: list[str] = Field(
        default_factory=list,
        description="Danh sách tất cả các scope keys phụ thuộc hoặc liên quan.",
    )
    description: str = Field(
        default="",
        description="Mô tả tóm tắt nội dung và mục đích của phiên bản ontology.",
    )
    compiler_version: str = Field(
        default="ontology-compiler-v2",
        description="Phiên bản của trình biên dịch Ontology đã tạo ra snapshot này.",
    )
    entity_types: list[FreeformDict] = Field(
        description="Danh sách định nghĩa các kiểu thực thể (Entity Types / Classes) và quy tắc định danh.",
    )
    properties: list[FreeformDict] = Field(
        description="Danh sách định nghĩa các thuộc tính, kiểu dữ liệu và ràng buộc giá trị.",
    )
    relationships: list[FreeformDict] = Field(
        description="Danh sách định nghĩa các mối quan hệ cho phép giữa các thực thể.",
    )
    aliases: list[FreeformDict] = Field(
        description="Danh sách các từ đồng nghĩa, bí danh (aliases) hỗ trợ nhận diện thực thể/thuộc tính.",
    )


class OntologyScopeSummary(IngestionModel):
    """Thông tin tóm lược nhẹ (Lightweight Catalog Summary) về một phạm vi chuyên môn của Ontology."""

    id: str = Field(
        description="ID định danh của Scope Summary.",
    )
    ontology_version_id: str = Field(
        description="ID phiên bản ontology sở hữu scope này.",
    )
    scope_key: str = Field(
        description="Mã định danh phạm vi chuyên môn.",
    )
    description: str = Field(
        description="Mô tả về phạm vi chuyên môn.",
    )
    summary: FreeformDict = Field(
        default_factory=dict,
        description="Thống kê tóm tắt (số lượng entity types, relationships, properties).",
    )
    schema_hash: str | None = Field(
        default=None,
        description="Mã hash của schema trong scope.",
    )


class ActiveOntology(IngestionModel):
    """Metadata đại diện cho phiên bản Ontology đang được kích hoạt (Active) trong hệ thống."""

    version_id: str = Field(
        description="ID của phiên bản ontology đang hoạt động.",
    )
    version: str = Field(
        description="Tên phiên bản ontology (ví dụ: 'v1.0.0')."
    )
