import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OntologyVersionStatus(str, Enum):
    """Trạng thái của phiên bản Ontology (Bản nháp, Đang hoạt động, Đã lưu trữ)."""

    DRAFT = "DRAFT"  # Bản nháp: Đang trong quá trình chỉnh sửa / xây dựng schema
    ACTIVE = "ACTIVE"  # Đang hoạt động: Schema chính thức áp dụng cho hệ thống
    ARCHIVED = "ARCHIVED"  # Đã lưu trữ: Phiên bản cũ đã ngưng sử dụng


class OntologyVersion(Base):
    """Bảng quản lý các phiên bản Ontology của hệ thống."""

    __tablename__ = "ontology_version"
    __table_args__ = {"schema": "ontology"}

    # Định danh duy nhất cho từng phiên bản Ontology (UUID v4)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Mã phiên bản (Ví dụ: 'v1.0.0', 'v2.1.0-draft') - Duy nhất trong toàn bộ hệ thống
    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        index=True,
    )

    # Trạng thái hiện tại của phiên bản này
    status: Mapped[OntologyVersionStatus] = mapped_column(
        SQLEnum(
            OntologyVersionStatus,
            name="ontology_version_status",
        ),
        nullable=False,
        default=OntologyVersionStatus.DRAFT,
        index=True,
    )

    # Mô tả chi tiết về mục đích hoặc thông tin của phiên bản này
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Người hoặc hệ thống khởi tạo phiên bản này
    created_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    # Thời điểm tạo phiên bản
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Thời điểm phiên bản này được kích hoạt sử dụng (status đổi thành ACTIVE)
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
