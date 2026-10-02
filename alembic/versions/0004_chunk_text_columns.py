"""Reserved revision: ingestion chunks are not stored in PostgreSQL.

Revision ID: 0004_chunk_text_columns
Revises: 0003_batch_scope
"""

revision = "0004_chunk_text_columns"
down_revision = "0003_batch_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
