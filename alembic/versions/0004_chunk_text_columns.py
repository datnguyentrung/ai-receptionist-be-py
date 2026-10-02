"""Alter ingestion_chunk columns section and source_anchor to Text.

Revision ID: 0004_chunk_text_columns
Revises: 0003_batch_scope
Create Date: 2026-10-02
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_chunk_text_columns"
down_revision = "0003_batch_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "ingestion_chunk",
        "source_anchor",
        existing_type=sa.String(length=512),
        type_=sa.Text(),
        schema="ontology",
    )
    op.alter_column(
        "ingestion_chunk",
        "section",
        existing_type=sa.String(length=512),
        type_=sa.Text(),
        schema="ontology",
    )


def downgrade() -> None:
    op.alter_column(
        "ingestion_chunk",
        "source_anchor",
        existing_type=sa.Text(),
        type_=sa.String(length=512),
        schema="ontology",
    )
    op.alter_column(
        "ingestion_chunk",
        "section",
        existing_type=sa.Text(),
        type_=sa.String(length=512),
        schema="ontology",
    )
