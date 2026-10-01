import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
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


class OntologyPropertyDataType(str, Enum):
    """Kiểu dữ liệu hỗ trợ cho thuộc tính trong Ontology."""

    STRING = "STRING"  # Chuỗi ký tự (Văn bản)
    INTEGER = "INTEGER"  # Số nguyên
    FLOAT = "FLOAT"  # Số thực
    BOOLEAN = "BOOLEAN"  # Giá trị đúng / sai
    DATE = "DATE"  # Ngày tháng (YYYY-MM-DD)
    DATETIME = "DATETIME"  # Ngày giờ (Timestamp)
    UUID = "UUID"  # Chuỗi định danh UUID
    JSON = "JSON"  # Dữ liệu dạng JSON/JSONB


class OntologyProperty(Base):
    """Bảng lưu trữ thuộc tính (Property/Attribute) của một loại thực thể (Entity Type).

    Ví dụ: Thực thể 'Visitor' có thuộc tính 'full_name' (STRING), 'phone_number' (STRING).
    """

    __tablename__ = "ontology_property"

    __table_args__ = (
        # Đảm bảo tên kỹ thuật thuộc tính duy nhất trong cùng 1 loại thực thể
        UniqueConstraint(
            "entity_type_id",
            "technical_name",
            name="uq_ontology_property_entity_name",
        ),
    )

    # Khóa chính định danh thuộc tính (UUID v4)
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

    # Khóa ngoại trỏ tới loại thực thể sở hữu thuộc tính này
    entity_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "ontology_entity_type.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Tên kỹ thuật dùng cho code / API (VD: 'full_name', 'email')
    technical_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    # Tên hiển thị trên giao diện (VD: 'Họ và tên', 'Địa chỉ Email')
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Kiểu dữ liệu của thuộc tính (STRING, INTEGER, JSON, v.v.)
    data_type: Mapped[OntologyPropertyDataType] = mapped_column(
        SQLEnum(
            OntologyPropertyDataType,
            name="ontology_property_data_type",
        ),
        nullable=False,
    )

    # Cờ đánh dấu thuộc tính bắt buộc nhập hay không
    required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    # Cờ đánh dấu thuộc tính có thể chứa nhiều giá trị (Mảng/List) hay không
    multi_value: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    # Ràng buộc dữ liệu chi tiết dạng JSON (VD: min, max, regex pattern, enum options)
    constraints: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Mô tả chi tiết về thuộc tính
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Thời điểm khởi tạo thuộc tính
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
