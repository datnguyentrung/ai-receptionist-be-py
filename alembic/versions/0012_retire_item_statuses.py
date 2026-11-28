"""Retire APPROVED and ARCHIVED statuses on knowledge governance items.

Revision ID: 0012_retire_item_statuses
Revises: 0011_remove_center_profile_code
"""

from alembic import op

revision = "0012_retire_item_statuses"
down_revision = "0011_remove_center_profile_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `status` is VARCHAR(32) (not a Postgres ENUM), so normalize any legacy rows.
    op.execute(
        """
        UPDATE ontology.knowledge_governance_item
        SET status = 'DRAFT'
        WHERE status IN ('APPROVED', 'ARCHIVED')
        """
    )


def downgrade() -> None:
    pass
