import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OntologyAliasSource(str, Enum):
    """Nguồn gốc tạo ra bí danh (Alias)."""

    MANUAL = "MANUAL"  # Nhập thủ công bởi quản trị viên
    LLM = "LLM"  # Trích xuất / đề xuất tự động từ mô hình ngôn ngữ lớn (LLM)
    INGESTION = (
        "INGESTION"  # Thu thập tự động từ quá trình nạp dữ liệu (Ingestion Pipeline)
    )


class OntologyAlias(Base):
    """Bảng lưu trữ các tên gọi thay thế / bí danh (Alias / Synonyms).

    Bí danh giúp LLM và hệ thống trích xuất tri thức ánh xạ từ nhiều từ ngữ đồng nghĩa
    về đúng Entity Type, Property, hoặc Relationship tương ứng trong Ontology.
    """

    __tablename__ = "ontology_alias"

    __table_args__ = (
        # Ràng buộc CheckConstraint: Một Alias chỉ được gắn vào duy nhất 1 trong 3 đối tượng (Entity / Property / Relationship)
        CheckConstraint(
            """
            (
                (entity_type_id IS NOT NULL)::int +
                (property_id IS NOT NULL)::int +
                (relationship_id IS NOT NULL)::int
            ) = 1
            """,
            name="ck_ontology_alias_exactly_one_target",
        ),
        # Đảm bảo bí danh không bị lặp lại trong cùng một phiên bản Ontology
        UniqueConstraint(
            "ontology_version_id",
            "alias",
            name="uq_ontology_alias_version_alias",
        ),
        {"schema": "ontology"},
    )

    # Khóa chính định danh Alias (UUID v4)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Khóa ngoại trỏ đến phiên bản Ontology
    ontology_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_version.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Từ hoặc cụm từ đồng nghĩa / bí danh (VD: 'khách đến', 'người ghé thăm' cho Entity 'Visitor')
    alias: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    # Trỏ tới loại thực thể (nếu bí danh đại diện cho Entity Type)
    entity_type_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_entity_type.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    # Trỏ tới thuộc tính (nếu bí danh đại diện cho Property)
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_property.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    # Trỏ tới mối quan hệ (nếu bí danh đại diện cho Relationship)
    relationship_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_relationship.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    # Độ tin cậy của bí danh khi trích xuất bởi LLM (giá trị từ 0.0 -> 1.0)
    confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    # Nguồn gốc phát sinh bí danh (MANUAL, LLM, INGESTION)
    source: Mapped[OntologyAliasSource] = mapped_column(
        SQLEnum(
            OntologyAliasSource,
            name="ontology_alias_source",
        ),
        nullable=False,
        default=OntologyAliasSource.MANUAL,
    )

    # Thời điểm khởi tạo bí danh
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
