"""Add durable ingestion documents, jobs, chunks, batches, and cache."""

from alembic import op
from app.db.base import Base
from app.models.ingestion import (  # noqa: F401
    IngestionBatch,
    IngestionChunk,
    IngestionDocument,
    IngestionDocumentVersion,
    IngestionExtractionCache,
    IngestionJob,
)

revision = "0002_ingestion_workflow"
down_revision = "0001_ontology_baseline"
branch_labels = None
depends_on = None

TABLES = (
    "ingestion_document",
    "ingestion_document_version",
    "ingestion_job",
    "ingestion_chunk",
    "ingestion_batch",
    "ingestion_extraction_cache",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=False)
