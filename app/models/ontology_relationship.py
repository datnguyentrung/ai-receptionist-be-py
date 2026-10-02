import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RelationshipCardinality(str, Enum):
    """Bản số quan hệ giữa các thực thể (Cardinality)."""

    ONE_TO_ONE = "ONE_TO_ONE"  # Quan hệ 1 - 1
    ONE_TO_MANY = "ONE_TO_MANY"  # Quan hệ 1 - Nhiều
    MANY_TO_ONE = "MANY_TO_ONE"  # Quan hệ Nhiều - 1
    MANY_TO_MANY = "MANY_TO_MANY"  # Quan hệ Nhiều - Nhiều


class OntologyRelationship(Base):
    """Bảng lưu trữ quan hệ (Relationship) liên kết giữa các thực thể trong Ontology.

    Ví dụ: Thực thể 'Visitor' liên kết với 'MeetingRoom' qua quan hệ 'books_room' (MANY_TO_MANY).
    """

    __tablename__ = "ontology_relationship"

    __table_args__ = (
        # Đảm bảo duy nhất định nghĩa quan hệ giữa cùng 2 loại thực thể trong 1 phiên bản
        UniqueConstraint(
            "ontology_version_id",
            "technical_name",
            "source_entity_type_id",
            "target_entity_type_id",
            name="uq_ontology_relationship_definition",
        ),
        {"schema": "ontology"},
    )

    # Khóa chính định danh quan hệ (UUID v4)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Khóa ngoại trỏ tới phiên bản Ontology tương ứng
    ontology_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_version.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Tên kỹ thuật của mối quan hệ (VD: 'works_for', 'attends_meeting')
    technical_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    # Tên hiển thị trên giao diện (VD: 'Làm việc tại', 'Tham gia cuộc họp')
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Khóa ngoại trỏ đến loại thực thể nguồn (Source Entity)
    source_entity_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_entity_type.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Khóa ngoại trỏ đến loại thực thể đích (Target Entity)
    target_entity_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_entity_type.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Bản số thể hiện mối quan hệ (ONE_TO_MANY, MANY_TO_MANY, v.v.)
    cardinality: Mapped[RelationshipCardinality] = mapped_column(
        SQLEnum(
            RelationshipCardinality,
            name="ontology_relationship_cardinality",
        ),
        nullable=False,
        default=RelationshipCardinality.MANY_TO_MANY,
    )

    # Mô tả chi tiết về quan hệ này
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Cấu hình ràng buộc bổ sung dạng JSON (VD: điều kiện kích hoạt, thuộc tính đi kèm)
    constraints: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Thời điểm tạo mối quan hệ
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
