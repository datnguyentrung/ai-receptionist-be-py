"""Human commit approval and serialized knowledge revision.

Revision ID: 0008_ingestion_approval
Revises: 0007_fact_conflicts
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0008_ingestion_approval"
down_revision = "0007_fact_conflicts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingestion_commit_approval",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ingestion_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ontology.ingestion_workspace_checkpoint.job_id"), nullable=False),
        sa.Column("report_digest", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(255), nullable=False),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("function_call_id", sa.String(255), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="ontology",
    )
    op.create_index("ix_ingestion_commit_approval_ingestion_id", "ingestion_commit_approval", ["ingestion_id"], schema="ontology")
    op.create_table(
        "ingestion_knowledge_revision",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_ingestion_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="ontology",
    )
    op.execute("INSERT INTO ontology.ingestion_knowledge_revision (id, revision) VALUES (1, 0)")


def downgrade() -> None:
    op.drop_table("ingestion_knowledge_revision", schema="ontology")
    op.drop_index("ix_ingestion_commit_approval_ingestion_id", table_name="ingestion_commit_approval", schema="ontology")
    op.drop_table("ingestion_commit_approval", schema="ontology")
