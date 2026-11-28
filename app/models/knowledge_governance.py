"""SQLAlchemy models for persistent multi-tenant Knowledge Governance items and Layer 3 proposals."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KnowledgeGovernanceItem(Base):
    """Persistent multi-tenant knowledge item across FORM, RICH_TEXT, DOCUMENT, and FAQ."""

    __tablename__ = "knowledge_governance_item"
    __table_args__ = {"schema": "ontology"}

    item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scope_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_item_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False, default="PUBLIC")
    author_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    reviewer_user_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    form_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    structured_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    evidence: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    validator_outcomes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    accepted_facts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    rejected_facts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    quarantined_segments: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class KnowledgePublishJob(Base):
    """Durable outbox record for an asynchronous standard-form publication."""

    __tablename__ = "knowledge_publish_job"
    __table_args__ = {"schema": "ontology"}

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    target_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class KnowledgeTenantProposal(Base):
    """Persistent tenant-scoped Layer 3 schema proposal."""

    __tablename__ = "knowledge_tenant_proposal"
    __table_args__ = {"schema": "ontology"}

    proposal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    proposal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    technical_name: Mapped[str] = mapped_column(String(150), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(150), nullable=True)
    scope_key: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PROPOSED", index=True)
    author_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = ["KnowledgeGovernanceItem", "KnowledgePublishJob", "KnowledgeTenantProposal"]
