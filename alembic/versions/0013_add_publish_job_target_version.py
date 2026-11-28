"""Add target_version to ontology.knowledge_publish_job.

Revision ID: 0013_publish_job_target_version
Revises: 0012_retire_item_statuses
Create Date: 2026-11-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_publish_job_target_version"
down_revision = "0012_retire_item_statuses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_publish_job",
        sa.Column("target_version", sa.Integer(), nullable=False, server_default="1"),
        schema="ontology",
    )


def downgrade() -> None:
    op.drop_column("knowledge_publish_job", "target_version", schema="ontology")
