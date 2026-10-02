"""Persist the ontology scope selected for each ingestion batch."""

import sqlalchemy as sa
from alembic import op

revision = "0003_batch_scope"
down_revision = "0002_ingestion_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"]
        for column in inspector.get_columns("ingestion_batch", schema="ontology")
    }
    if "scope_key" not in columns:
        op.add_column(
            "ingestion_batch",
            sa.Column("scope_key", sa.String(length=50), nullable=True),
            schema="ontology",
        )
    op.execute(
        """
        UPDATE ontology.ingestion_batch AS batch
        SET scope_key = COALESCE(job.scope_hint, 'core')
        FROM ontology.ingestion_job AS job
        WHERE batch.job_id = job.id
        """
    )
    op.alter_column(
        "ingestion_batch",
        "scope_key",
        nullable=False,
        server_default="core",
        schema="ontology",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"]
        for column in inspector.get_columns("ingestion_batch", schema="ontology")
    }
    if "scope_key" in columns:
        op.drop_column("ingestion_batch", "scope_key", schema="ontology")
