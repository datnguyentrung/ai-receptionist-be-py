"""Persist multi-tenant knowledge governance items and tenant schema proposals.

Revision ID: 0010_knowledge_governance
Revises: 0009_entity_resolution_reviews
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_knowledge_governance"
down_revision = "0009_entity_resolution_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_governance_item",
        sa.Column("item_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("scope_key", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("parent_item_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False, server_default="PUBLIC"),
        sa.Column("author_user_id", sa.String(100), nullable=False),
        sa.Column("reviewer_user_id", sa.String(100), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("form_type", sa.String(64), nullable=True),
        sa.Column("structured_payload", postgresql.JSONB(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("validator_outcomes", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("accepted_facts", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("rejected_facts", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("conflicts", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("quarantined_segments", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        schema="ontology",
    )
    op.create_index("ix_kg_item_tenant_status", "knowledge_governance_item", ["tenant_id", "status"], schema="ontology")

    op.create_table(
        "knowledge_publish_job",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("item_id", sa.String(64), nullable=False, unique=True),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="ontology",
    )
    op.create_index("ix_kg_publish_job_tenant_status", "knowledge_publish_job", ["tenant_id", "status"], schema="ontology")

    op.create_table(
        "knowledge_tenant_proposal",
        sa.Column("proposal_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(100), nullable=False),
        sa.Column("proposal_type", sa.String(64), nullable=False),
        sa.Column("technical_name", sa.String(150), nullable=False),
        sa.Column("entity_type", sa.String(150), nullable=True),
        sa.Column("scope_key", sa.String(100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="PROPOSED"),
        sa.Column("author_user_id", sa.String(100), nullable=False),
        sa.Column("reviewed_by", sa.String(100), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="ontology",
    )
    op.create_index("ix_kg_proposal_tenant_status", "knowledge_tenant_proposal", ["tenant_id", "status"], schema="ontology")


def downgrade() -> None:
    op.drop_index("ix_kg_publish_job_tenant_status", table_name="knowledge_publish_job", schema="ontology")
    op.drop_table("knowledge_publish_job", schema="ontology")
    op.drop_index("ix_kg_proposal_tenant_status", table_name="knowledge_tenant_proposal", schema="ontology")
    op.drop_table("knowledge_tenant_proposal", schema="ontology")
    op.drop_index("ix_kg_item_tenant_status", table_name="knowledge_governance_item", schema="ontology")
    op.drop_table("knowledge_governance_item", schema="ontology")
