"""Durable human decisions for non-deterministic entity candidates."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionEntityResolutionReview(Base):
    __tablename__ = "ingestion_entity_resolution_review"
    __table_args__ = (
        UniqueConstraint("ingestion_id", "extracted_key", "candidate_key", name="uq_ingestion_entity_resolution_review"),
        {"schema": "ontology"},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingestion_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    extracted_key: Mapped[str] = mapped_column(String(128), index=True)
    candidate_key: Mapped[str] = mapped_column(String(128), index=True)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
