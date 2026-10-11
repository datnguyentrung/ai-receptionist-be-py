"""LLM extraction contracts (Semantic Layer without business identity)."""

from typing import Any
from pydantic import Field, model_validator

from app.schemas.ingestion.base import IngestionModel
from app.schemas.ingestion.coverage import ChunkCoverage
from app.schemas.ingestion.grounding import Evidence, PropertyFact


class SemanticGraphNode(IngestionModel):
    """Thực thể (Node) ngữ nghĩa được trích xuất trực tiếp bởi LLM.

    Đặc điểm thiết kế: Cố tình KHÔNG chứa trường `identity` nghiệp vụ.
    Lý do: LLM chỉ chịu trách nhiệm nhận diện thực thể và gán ID tạm thời (temp_id),
    việc định danh và gán identity duy nhất sẽ do Ingestion Engine xử lý.
    """

    temp_id: str = Field(
        default="",
        description="ID tạm thời do LLM tạo ra trong phạm vi batch trích xuất (ví dụ: 'node_1', 'the_thuc_1').",
    )
    class_name: str = Field(
        min_length=1,
        description="Tên lớp thực thể định nghĩa trong Ontology (ví dụ: 'TheThucQuyen', 'DonDanh').",
    )
    properties: list[PropertyFact] = Field(
        default_factory=list,
        description="Danh sách các thuộc tính kèm bằng chứng của thực thể này.",
    )
    evidence: list[Evidence] = Field(
        default_factory=list,
        description="Danh sách bằng chứng tổng quát chứng minh sự tồn tại của thực thể.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Độ tin cậy của việc trích xuất thực thể (giá trị từ 0.0 đến 1.0).",
    )


class SemanticGraphEdge(IngestionModel):
    """Mối quan hệ (Edge) ngữ nghĩa nối giữa hai thực thể do LLM trích xuất."""

    edge_name: str = Field(
        min_length=1,
        description="Tên mối quan hệ định nghĩa trong Ontology (ví dụ: 'BAO_GOM_DONG_TAC', 'THUOC_VE_QUYEN').",
    )
    source_temp_id: str = Field(
        min_length=1,
        description="temp_id của node nguồn (Source Node).",
    )
    target_temp_id: str = Field(
        min_length=1,
        description="temp_id của node đích (Target Node).",
    )
    properties: list[PropertyFact] = Field(
        default_factory=list,
        description="Các thuộc tính mô tả bổ sung cho mối quan hệ kèm theo bằng chứng.",
    )
    evidence: list[Evidence] = Field(
        min_length=1,
        description="Bằng chứng chứng minh sự tồn tại của mối quan hệ này trong văn bản.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Độ tin cậy của mối quan hệ (giá trị từ 0.0 đến 1.0).",
    )


class SemanticGraphPatchFragment(IngestionModel):
    """Hợp đồng (Contract) duy nhất trả về từ LLM cho một batch trích xuất.

    Bao gồm các nodes/edges trích xuất được cùng với bảng đối soát độ phủ (coverage)
    cho toàn bộ các chunks nằm trong batch đó.
    """

    nodes: list[SemanticGraphNode] = Field(
        default_factory=list,
        description="Danh sách thực thể ngữ nghĩa do LLM nhận diện trong batch.",
    )
    edges: list[SemanticGraphEdge] = Field(
        default_factory=list,
        description="Danh sách mối quan hệ ngữ nghĩa nối các thực thể trong batch.",
    )
    coverage: list[ChunkCoverage] = Field(
        min_length=1,
        description="Báo cáo độ bao phủ bắt buộc cho tất cả các chunks có trong batch.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Danh sách các cảnh báo hoặc lưu ý đặc biệt do LLM ghi nhận khi trích xuất.",
    )


class SemanticGraphRepairDelta(IngestionModel):
    """Mảnh dữ liệu sửa chữa (Repair Delta) được trả về bởi LLM trong phase repair.

    Cấu trúc này ép buộc LLM chỉ trả về delta:
    - repaired_nodes: Chỉ chứa các node cần sửa thuộc tính hoặc bổ sung mới.
    - repaired_edges: Chỉ chứa các quan hệ mới cần bổ sung hoặc sửa đổi.
    - dropped_temp_ids: Liệt kê các tempId cần loại bỏ nếu không hợp lệ hoặc không sửa được.
    - coverage_updates: Cập nhật độ bao phủ cho các chunk bị lỗi.
    """

    baseline_fingerprint: str = Field(
        min_length=1,
        description="SHA-256 fingerprint của protected baseline mà delta này sửa chữa.",
    )
    repaired_nodes: list[SemanticGraphNode] = Field(
        default_factory=list,
        description="CHỈ chứa các node cần sửa lỗi thuộc tính hoặc thêm mới. KHÔNG đưa lại các node baseline đã hợp lệ.",
    )
    repaired_edges: list[SemanticGraphEdge] = Field(
        default_factory=list,
        description="CHỈ chứa các quan hệ cần sửa hoặc thêm mới.",
    )
    dropped_temp_ids: list[str] = Field(
        default_factory=list,
        description="Danh sách tempId của các node/edge không thể sửa và cần loại bỏ.",
    )
    coverage_updates: list[ChunkCoverage] = Field(
        default_factory=list,
        description="Cập nhật độ bao phủ cho các chunk bị lỗi.",
    )

    @model_validator(mode="before")
    @classmethod
    def _remap_legacy_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "nodes" in data and "repaired_nodes" not in data:
                data["repaired_nodes"] = data.pop("nodes")
            if "edges" in data and "repaired_edges" not in data:
                data["repaired_edges"] = data.pop("edges")
            if "coverage" in data and "coverage_updates" not in data:
                data["coverage_updates"] = data.pop("coverage")
        return data

    @property
    def nodes(self) -> list[SemanticGraphNode]:
        return self.repaired_nodes

    @property
    def edges(self) -> list[SemanticGraphEdge]:
        return self.repaired_edges

    @property
    def coverage(self) -> list[ChunkCoverage]:
        return self.coverage_updates
