"""Durable checkpoints for resumable ingestion jobs."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionWorkspaceCheckpoint(Base):
    """A complete workspace snapshot; JSON keeps the evolving batch contract atomic."""

    __tablename__ = "ingestion_workspace_checkpoint"
    __table_args__ = {"schema": "ontology"}

    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ingestion_signature: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    document_key: Mapped[str] = mapped_column(String(1024), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
