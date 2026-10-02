"""Reserved revision: batch scope is not stored in PostgreSQL.

Revision ID: 0003_batch_scope
Revises: 0002_ingestion_workflow
"""

revision = "0003_batch_scope"
down_revision = "0002_ingestion_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
