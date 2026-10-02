import uuid
from datetime import datetime
from enum import Enum
from typing import ClassVar

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SchemaProposalType(str, Enum):
    """Loại đề xuất thay đổi cấu trúc Ontology Schema."""

    NEW_ENTITY_TYPE = "NEW_ENTITY_TYPE"  # Đề xuất tạo loại thực thể mới
    NEW_PROPERTY = "NEW_PROPERTY"  # Đề xuất thêm thuộc tính mới
    NEW_RELATIONSHIP = "NEW_RELATIONSHIP"  # Đề xuất thêm quan hệ mới
    NEW_ALIAS = "NEW_ALIAS"  # Đề xuất thêm bí danh mới
    MODIFY_ENTITY_TYPE = "MODIFY_ENTITY_TYPE"  # Đề xuất chỉnh sửa thực thể hiện có
    MODIFY_PROPERTY = "MODIFY_PROPERTY"  # Đề xuất chỉnh sửa thuộc tính hiện có
    MODIFY_RELATIONSHIP = "MODIFY_RELATIONSHIP"  # Đề xuất chỉnh sửa quan hệ hiện có
    NEW_SCOPE = "NEW_SCOPE"
    MODIFY_SCOPE = "MODIFY_SCOPE"


class SchemaProposalStatus(str, Enum):
    """Trạng thái duyệt đề xuất Schema."""

    PROPOSED = "PROPOSED"  # Mới đề xuất, chờ duyệt
    APPROVED = "APPROVED"  # Đã chấp thuận bởi quản trị viên
    REJECTED = "REJECTED"  # Đã từ chối
    APPLIED = "APPLIED"  # Đã áp dụng chính thức vào Ontology Version


class OntologySchemaProposal(Base):
    """Bảng lưu trữ các đề xuất thay đổi / mở rộng Schema Ontology.

    Cho phép LLM hoặc Agent tự động phát hiện tri thức mới từ tài liệu/hội thoại
    và đưa ra đề xuất cho con người (Human-in-the-loop) duyệt trước khi cập nhật chính thức.
    """

    __tablename__ = "ontology_schema_proposal"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "ontology"}

    # Khóa chính định danh đề xuất (UUID v4)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Khóa ngoại trỏ đến phiên bản Ontology áp dụng đề xuất này
    ontology_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_version.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    applied_ontology_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ontology_version.id"), nullable=True, index=True
    )

    source_ingestion_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    source_batch_index: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )

    affected_scope_keys: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    proposal_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    # Loại đề xuất (NEW_ENTITY_TYPE, NEW_PROPERTY, v.v.)
    proposal_type: Mapped[SchemaProposalType] = mapped_column(
        SQLEnum(
            SchemaProposalType,
            name="ontology_schema_proposal_type",
        ),
        nullable=False,
        index=True,
    )

    # Trạng thái phê duyệt (PROPOSED, APPROVED, REJECTED, APPLIED)
    status: Mapped[SchemaProposalStatus] = mapped_column(
        SQLEnum(
            SchemaProposalStatus,
            name="ontology_schema_proposal_status",
        ),
        nullable=False,
        default=SchemaProposalStatus.PROPOSED,
        index=True,
    )

    # Tên kỹ thuật của đối tượng đề xuất (nếu có)
    technical_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    # Lý do / Giải trình đề xuất thay đổi này
    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Nội dung cấu hình chi tiết đề xuất dạng JSON (định nghĩa thuộc tính, loại thực thể mới, v.v.)
    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    # Bằng chứng trích xuất dạng JSON (VD: đoạn trích văn bản, câu hội thoại mà Agent phát hiện)
    evidence: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Mã định danh tài liệu nguồn phát sinh ra đề xuất này
    source_document_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    # Người thực hiện duyệt / từ chối đề xuất
    reviewed_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # Thời điểm phê duyệt hoặc từ chối
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Thời điểm khởi tạo đề xuất
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
