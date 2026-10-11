"""Human decisions for contradictory, source-backed assertions."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionFactConflict(Base):
    __tablename__ = "ingestion_fact_conflict"
    __table_args__ = (
        UniqueConstraint("new_fact_id", "old_fact_id", name="uq_ingestion_fact_conflict_pair"),
        {"schema": "ontology"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingestion_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    new_fact_id: Mapped[str] = mapped_column(String(128), index=True)
    old_fact_id: Mapped[str] = mapped_column(String(128), index=True)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
