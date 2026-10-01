import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OntologyEntityType(Base):
    """Bảng định nghĩa các loại thực thể (Entity Type) thuộc một phiên bản Ontology.

    Ví dụ: 'Visitor' (Khách hàng), 'Employee' (Nhân viên), 'MeetingRoom' (Phòng họp).
    """

    __tablename__ = "ontology_entity_type"

    __table_args__ = (
        # Đảm bảo tên kỹ thuật là duy nhất trong cùng một phiên bản Ontology
        UniqueConstraint(
            "ontology_version_id",
            "technical_name",
            name="uq_ontology_entity_type_version_name",
        ),
    )

    # Khóa chính định danh thực thể (UUID v4)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Khóa ngoại trỏ đến phiên bản Ontology chứa loại thực thể này
    ontology_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_version.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Tên kỹ thuật dùng cho code/system (VD: 'visitor', 'employee')
    technical_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )

    # Tên hiển thị giao diện thân thiện với người dùng (VD: 'Khách tham quan', 'Nhân viên')
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Mô tả chi tiết chức năng và vai trò của loại thực thể này
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Chiến lược định danh duy nhất cho thực thể (VD: cấu hình trường bắt buộc để nhận dạng)
    identity_strategy: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Thông tin bổ sung linh hoạt dạng JSON (VD: icon, color, UI layout config)
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    # Thời điểm khởi tạo thực thể
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
