"""Persist ingestion workspaces so a restart can resume safely.

Revision ID: 0006_ingestion_checkpoint
Revises: 0005_dynamic_ontology_ingestion
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0006_ingestion_checkpoint"
down_revision = "0005_dynamic_ontology_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingestion_workspace_checkpoint",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ingestion_signature", sa.String(64), nullable=False, unique=True),
        sa.Column("document_key", sa.String(1024), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="ontology",
    )
    op.create_index("ix_ingestion_checkpoint_document_key", "ingestion_workspace_checkpoint", ["document_key"], schema="ontology")


def downgrade() -> None:
    op.drop_table("ingestion_workspace_checkpoint", schema="ontology")
