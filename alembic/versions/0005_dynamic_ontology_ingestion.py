"""Dynamic ontology scopes, compiled snapshots, and schema proposals.

Revision ID: 0005_dynamic_ontology_ingestion
Revises: 0004_chunk_text_columns
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision = "0005_dynamic_ontology_ingestion"
down_revision = "0004_chunk_text_columns"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table, schema="ontology")}


def upgrade() -> None:
    bind = op.get_bind()
    offline = context.is_offline_mode()
    inspector = None if offline else sa.inspect(bind)

    op.execute(
        "ALTER TYPE ontology_schema_proposal_type ADD VALUE IF NOT EXISTS 'NEW_SCOPE'"
    )
    op.execute(
        "ALTER TYPE ontology_schema_proposal_type ADD VALUE IF NOT EXISTS 'MODIFY_SCOPE'"
    )

    version_columns = {"parent_version_id"} if offline else _columns(inspector, "ontology_version")
    if "parent_version_id" not in version_columns:
        op.add_column(
            "ontology_version",
            sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
            schema="ontology",
        )
        op.create_foreign_key(
            "fk_ontology_version_parent",
            "ontology_version",
            "ontology_version",
            ["parent_version_id"],
            ["id"],
            source_schema="ontology",
            referent_schema="ontology",
            ondelete="SET NULL",
        )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_ontology_single_active "
        "ON ontology.ontology_version ((status)) WHERE status = 'ACTIVE'"
    )

    tables = set() if offline else set(inspector.get_table_names(schema="ontology"))
    if offline or "ontology_scope" not in tables:
        op.create_table(
            "ontology_scope",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("ontology_version_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("scope_key", sa.String(100), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("summary", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["ontology_version_id"], ["ontology.ontology_version.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("ontology_version_id", "scope_key", name="uq_ontology_scope_version_key"),
            schema="ontology",
        )
    if offline or "ontology_entity_type_scope" not in tables:
        op.create_table(
            "ontology_entity_type_scope",
            sa.Column("ontology_version_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("scope_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("entity_type_id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.ForeignKeyConstraint(["ontology_version_id"], ["ontology.ontology_version.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["scope_id"], ["ontology.ontology_scope.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["entity_type_id"], ["ontology.ontology_entity_type.id"], ondelete="CASCADE"),
            schema="ontology",
        )
    op.execute(
        """
        INSERT INTO ontology.ontology_scope
            (id, ontology_version_id, scope_key, description, summary)
        SELECT DISTINCT
            md5(entity.ontology_version_id::text || ':' || extracted.scope_key)::uuid,
            entity.ontology_version_id,
            lower(extracted.scope_key),
            'Migrated ontology scope ' || lower(extracted.scope_key),
            '{}'::jsonb
        FROM ontology.ontology_entity_type AS entity
        CROSS JOIN LATERAL jsonb_array_elements_text(
            CASE
              WHEN jsonb_typeof(entity.metadata->'scopes') = 'array'
              THEN entity.metadata->'scopes'
              ELSE '[]'::jsonb
            END
        ) AS extracted(scope_key)
        ON CONFLICT (ontology_version_id, scope_key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO ontology.ontology_entity_type_scope
            (ontology_version_id, scope_id, entity_type_id)
        SELECT entity.ontology_version_id, scope.id, entity.id
        FROM ontology.ontology_entity_type AS entity
        CROSS JOIN LATERAL jsonb_array_elements_text(
            CASE
              WHEN jsonb_typeof(entity.metadata->'scopes') = 'array'
              THEN entity.metadata->'scopes'
              ELSE '[]'::jsonb
            END
        ) AS extracted(scope_key)
        JOIN ontology.ontology_scope AS scope
          ON scope.ontology_version_id = entity.ontology_version_id
         AND scope.scope_key = lower(extracted.scope_key)
        ON CONFLICT DO NOTHING
        """
    )

    snapshot_columns = {"description", "compiler_version", "updated_at"} if offline else _columns(inspector, "ontology_compiled_snapshot")
    for name, column in (
        ("description", sa.Column("description", sa.Text(), nullable=False, server_default="")),
        ("compiler_version", sa.Column("compiler_version", sa.String(50), nullable=False, server_default="ontology-compiler-v2")),
        ("updated_at", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())),
    ):
        if name not in snapshot_columns:
            op.add_column("ontology_compiled_snapshot", column, schema="ontology")

    proposal_columns = {
        "applied_ontology_version_id", "source_ingestion_id", "source_batch_index",
        "affected_scope_keys", "proposal_digest",
    } if offline else _columns(inspector, "ontology_schema_proposal")
    additions = (
        ("applied_ontology_version_id", sa.Column("applied_ontology_version_id", postgresql.UUID(as_uuid=True), nullable=True)),
        ("source_ingestion_id", sa.Column("source_ingestion_id", sa.String(64), nullable=True)),
        ("source_batch_index", sa.Column("source_batch_index", sa.Integer(), nullable=True)),
        ("affected_scope_keys", sa.Column("affected_scope_keys", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb"))),
        ("proposal_digest", sa.Column("proposal_digest", sa.String(64), nullable=True)),
    )
    added_proposal_columns: set[str] = set()
    for name, column in additions:
        if name not in proposal_columns:
            op.add_column("ontology_schema_proposal", column, schema="ontology")
            added_proposal_columns.add(name)
    op.create_index("uq_ontology_schema_proposal_digest", "ontology_schema_proposal", ["proposal_digest"], unique=True, schema="ontology", if_not_exists=True)
    if "applied_ontology_version_id" in added_proposal_columns:
        op.create_foreign_key(
            "fk_schema_proposal_applied_version", "ontology_schema_proposal",
            "ontology_version", ["applied_ontology_version_id"], ["id"],
            source_schema="ontology", referent_schema="ontology",
        )


def downgrade() -> None:
    op.drop_table("ontology_entity_type_scope", schema="ontology")
    op.drop_table("ontology_scope", schema="ontology")
