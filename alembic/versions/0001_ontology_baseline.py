"""Baseline for the seven pre-existing ontology tables."""

from alembic import op
from app.db.base import Base
from app.models import (  # noqa: F401
    OntologyAlias,
    OntologyCompiledSnapshot,
    OntologyEntityType,
    OntologyProperty,
    OntologyRelationship,
    OntologySchemaProposal,
    OntologyVersion,
)

revision = "0001_ontology_baseline"
down_revision = None
branch_labels = None
depends_on = None

TABLES = (
    "ontology_version",
    "ontology_entity_type",
    "ontology_property",
    "ontology_relationship",
    "ontology_alias",
    "ontology_schema_proposal",
    "ontology_compiled_snapshot",
)


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE SCHEMA IF NOT EXISTS ontology;")
    for name in TABLES:
        table = Base.metadata.tables.get(name) or Base.metadata.tables.get(f"ontology.{name}")
        if table is not None:
            table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLES):
        table = Base.metadata.tables.get(name) or Base.metadata.tables.get(f"ontology.{name}")
        if table is not None:
            table.drop(bind=bind, checkfirst=True)

