"""Durable record of a human-confirmed ingestion commit."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionCommitApproval(Base):
    __tablename__ = "ingestion_commit_approval"
    __table_args__ = {"schema": "ontology"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ontology.ingestion_workspace_checkpoint.job_id"), index=True
    )
    report_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    function_call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestionKnowledgeRevision(Base):
    __tablename__ = "ingestion_knowledge_revision"
    __table_args__ = {"schema": "ontology"}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_ingestion_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
