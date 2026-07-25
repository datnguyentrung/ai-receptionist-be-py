import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Column, Date, DateTime, Enum, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.session import Base
from app.enums.core_enum import Belt


class Person(Base):
    __tablename__ = "person"
    __table_args__ = (
        Index("idx_person_full_name", "full_name"),
        Index("idx_person_national_code", "national_code"),
        {"schema": "core"},
    )

    person_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name = Column(String(100), nullable=False)
    gender = Column(Boolean)
    birth_date = Column(Date)
    email = Column(String(100))
    national_code = Column(String(50), unique=True)
    belt = Column(Enum(Belt), nullable=False, default=Belt.C10)
    face_embedding = Column(Vector(512))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
