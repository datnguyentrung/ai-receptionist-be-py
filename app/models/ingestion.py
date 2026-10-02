"""Durable PostgreSQL metadata for the ingestion workflow."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionDocument(Base):
    __tablename__ = "ingestion_document"
    __table_args__ = {"schema": "ontology"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, default="MANUAL_UPLOAD")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class IngestionDocumentVersion(Base):
    __tablename__ = "ingestion_document_version"
    __table_args__ = (
        UniqueConstraint("document_id", "ingestion_signature", name="uq_ingestion_document_signature"),
        {"schema": "ontology"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_document.id", ondelete="CASCADE"), nullable=False, index=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    config_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    ingestion_signature: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ontology_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ontology_version.id"), nullable=False, index=True)
    ontology_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    skill_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(50), nullable=False)
    mapper_version: Mapped[str] = mapped_column(String(50), nullable=False)
    compiler_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IngestionJob(Base):
    __tablename__ = "ingestion_job"
    __table_args__ = {"schema": "ontology"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_document_version.id", ondelete="CASCADE"), nullable=False, index=True)
    ontology_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ontology_version.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="RECEIVED", index=True)
    stage: Mapped[str] = mapped_column(String(80), nullable=False, default="received")
    scope_hint: Mapped[str | None] = mapped_column(String(50), nullable=True)
    readiness_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IngestionChunk(Base):
    __tablename__ = "ingestion_chunk"
    __table_args__ = (
        UniqueConstraint("document_version_id", "chunk_index", name="uq_ingestion_chunk_version_index"),
        UniqueConstraint("chunk_id", name="uq_ingestion_chunk_id"),
        {"schema": "ontology"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_document_version.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(512), nullable=True)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_anchor: Mapped[str] = mapped_column(String(512), nullable=False)


class IngestionBatch(Base):
    __tablename__ = "ingestion_batch"
    __table_args__ = (
        UniqueConstraint("job_id", "batch_index", name="uq_ingestion_batch_job_index"),
        {"schema": "ontology"},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_job.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_indexes: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING", index=True)
    graph_fragment: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validation_issues: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    validation_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class IngestionExtractionCache(Base):
    __tablename__ = "ingestion_extraction_cache"
    __table_args__ = {"schema": "ontology"}

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("ingestion_document_version.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_index: Mapped[int] = mapped_column(Integer, nullable=False)
    graph_context_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    fragment: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


__all__ = [
    "IngestionBatch",
    "IngestionChunk",
    "IngestionDocument",
    "IngestionDocumentVersion",
    "IngestionExtractionCache",
    "IngestionJob",
]
