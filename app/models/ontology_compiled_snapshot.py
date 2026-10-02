import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OntologyCompiledSnapshot(Base):
    """Bảng lưu trữ bản chụp Schema đã biên dịch (Compiled Snapshot).

    Giúp tối ưu hiệu năng: Thay vì mỗi lần LLM truy vấn phải query JOIN nhiều bảng
    (Entity, Property, Relationship, Alias), hệ thống sẽ biên dịch sẵn thành file JSON Schema
    và cache lại snapshot theo scope_key.
    """

    __tablename__ = "ontology_compiled_snapshot"

    __table_args__ = (
        # Đảm bảo duy nhất 1 bản compiled snapshot cho mỗi scope_key thuộc 1 phiên bản
        UniqueConstraint(
            "ontology_version_id",
            "scope_key",
            name="uq_ontology_compiled_snapshot_version_scope",
        ),
        {"schema": "ontology"},
    )

    # Khóa chính định danh Snapshot (UUID v4)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Khóa ngoại trỏ đến phiên bản Ontology được biên dịch
    ontology_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_version.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Phạm vi/Khóa phân loại snapshot (VD: 'full_schema', 'receptionist_agent_context', 'domain_hr')
    scope_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    # Mã băm SHA256 của schema nhằm phát hiện thay đổi dữ liệu để re-compile
    schema_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    # Toàn bộ cấu trúc Schema đã đóng gói sẵn dưới dạng JSONB cho LLM Prompt / Engine
    compiled_schema: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
    )

    # Thời điểm biên dịch snapshot
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Thời điểm hết hạn cache snapshot (nếu có cấu hình TTL)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
