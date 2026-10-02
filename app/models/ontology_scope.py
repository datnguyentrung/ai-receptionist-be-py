"""Normalized scope membership for a versioned ontology."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OntologyScope(Base):
    __tablename__ = "ontology_scope"
    __table_args__ = (
        UniqueConstraint(
            "ontology_version_id", "scope_key", name="uq_ontology_scope_version_key"
        ),
        {"schema": "ontology"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ontology_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_version.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OntologyEntityTypeScope(Base):
    __tablename__ = "ontology_entity_type_scope"
    __table_args__ = (
        UniqueConstraint(
            "scope_id", "entity_type_id", name="uq_ontology_entity_type_scope"
        ),
        {"schema": "ontology"},
    )

    ontology_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_version.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_scope.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ontology_entity_type.id", ondelete="CASCADE"),
        primary_key=True,
    )


__all__ = ["OntologyEntityTypeScope", "OntologyScope"]
