"""Resolved Graph Engine contracts with identities and ontology bindings."""

from pydantic import Field

from app.schemas.ingestion.base import FreeformDict, IngestionModel
from app.schemas.ingestion.coverage import ChunkCoverage
from app.schemas.ingestion.grounding import Evidence, PropertyFact


class GraphNode(IngestionModel):
    """Thực thể (Node) nội bộ của Ingestion Engine sau khi đã phân giải danh tính (Identity Resolution)."""

    temp_id: str = Field(
        min_length=1,
        description="ID tạm thời dùng để map các liên kết trong cùng bản vá (patch).",
    )
    class_name: str = Field(
        min_length=1,
        description="Tên lớp thực thể theo Ontology.",
    )
    identity: FreeformDict = Field(
        default_factory=dict,
        description="Tập thuộc tính định danh duy nhất (Unique Identity) của thực thể dùng để merge node.",
    )
    properties: list[PropertyFact] = Field(
        default_factory=list,
        description="Danh sách thuộc tính của node kèm bằng chứng.",
    )
    evidence: list[Evidence] = Field(
        default_factory=list,
        description="Danh sách bằng chứng chứng minh sự tồn tại của node.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Độ tin cậy của node.",
    )


# Alias tương thích
ExtractedNode = GraphNode


class GraphEdge(IngestionModel):
    """Mối quan hệ (Edge) nội bộ của Ingestion Engine với thuộc tính đã được resolved."""

    edge_name: str = Field(
        min_length=1,
        description="Tên mối quan hệ theo Ontology.",
    )
    source_temp_id: str = Field(
        min_length=1,
        description="temp_id của node nguồn.",
    )
    target_temp_id: str = Field(
        min_length=1,
        description="temp_id của node đích.",
    )
    properties: FreeformDict = Field(
        default_factory=dict,
        description="Dict thuộc tính của mối quan hệ sau khi đã làm sạch/phân giải.",
    )
    evidence: list[Evidence] = Field(
        min_length=1,
        description="Danh sách bằng chứng chứng minh mối quan hệ.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Độ tin cậy của mối quan hệ.",
    )


# Alias tương thích
ExtractedEdge = GraphEdge


class GraphPatchFragment(IngestionModel):
    """Mảnh đồ thị trích xuất nội bộ gắn liền với phiên bản Ontology cụ thể."""

    ontology_version: str = Field(
        default="v1",
        min_length=1,
        description="Phiên bản Ontology dùng để đối soát và xây dựng bản vá này.",
    )
    nodes: list[GraphNode] = Field(
        default_factory=list,
        description="Danh sách nodes đã resolved identity.",
    )
    edges: list[GraphEdge] = Field(
        default_factory=list,
        description="Danh sách edges đã resolved.",
    )
    coverage: list[ChunkCoverage] = Field(
        default_factory=list,
        description="Báo cáo độ phủ của các chunk cấu thành nên fragment này.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Danh sách cảnh báo phát sinh trong quá trình chuẩn hóa fragment.",
    )


class GraphPatchDraft(GraphPatchFragment):
    """Bản phác thảo đồ thị tri thức tổng thể (Draft Patch) sau khi gộp các fragment.

    Kế thừa cấu trúc của `GraphPatchFragment` nhưng đại diện cho toàn bộ tri thức
    đã được hợp nhất từ tất cả các batch của tài liệu.
    """


__all__ = [
    "ExtractedEdge",
    "ExtractedNode",
    "GraphEdge",
    "GraphNode",
    "GraphPatchDraft",
    "GraphPatchFragment",
]
