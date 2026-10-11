"""Store durable human review for conflicting source facts.

Revision ID: 0007_fact_conflicts
Revises: 0006_ingestion_checkpoint
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0007_fact_conflicts"
down_revision = "0006_ingestion_checkpoint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingestion_fact_conflict",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ingestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("new_fact_id", sa.String(128), nullable=False),
        sa.Column("old_fact_id", sa.String(128), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("new_fact_id", "old_fact_id", name="uq_ingestion_fact_conflict_pair"),
        schema="ontology",
    )
    for column in ("ingestion_id", "new_fact_id", "old_fact_id", "decision"):
        op.create_index(f"ix_ingestion_fact_conflict_{column}", "ingestion_fact_conflict", [column], schema="ontology")


def downgrade() -> None:
    op.drop_table("ingestion_fact_conflict", schema="ontology")
