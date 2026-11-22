"""Persist human review of semantic entity candidates.

Revision ID: 0009_entity_resolution_reviews
Revises: 0008_ingestion_approval
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_entity_resolution_reviews"
down_revision = "0008_ingestion_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingestion_entity_resolution_review",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ingestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extracted_key", sa.String(128), nullable=False),
        sa.Column("candidate_key", sa.String(128), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("note", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("ingestion_id", "extracted_key", "candidate_key", name="uq_ingestion_entity_resolution_review"),
        schema="ontology",
    )
    for column in ("ingestion_id", "extracted_key", "candidate_key", "decision"):
        op.create_index(f"ix_ingestion_entity_resolution_review_{column}", "ingestion_entity_resolution_review", [column], schema="ontology")


def downgrade() -> None:
    op.drop_table("ingestion_entity_resolution_review", schema="ontology")
