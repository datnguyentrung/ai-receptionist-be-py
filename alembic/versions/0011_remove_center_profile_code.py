"""Retire the editable Center Profile code in favor of tenant singleton identity.

Revision ID: 0011_remove_center_profile_code
Revises: 0010_knowledge_governance
"""

from alembic import op


revision = "0011_remove_center_profile_code"
down_revision = "0010_knowledge_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Center Profile is a tenant singleton.  Its identity is derived from the
    # trusted tenant context, never from a user-editable payload property.
    op.execute("""
        UPDATE ontology.ontology_entity_type
        SET identity_strategy = '{"required": []}'::jsonb
        WHERE technical_name = 'center_profile'
    """)
    op.execute("""
        DELETE FROM ontology.ontology_property AS property
        USING ontology.ontology_entity_type AS entity
        WHERE property.entity_type_id = entity.id
          AND entity.technical_name = 'center_profile'
          AND property.technical_name = 'profile_code'
    """)

    # Remove the retired field from persisted form drafts as well.  This keeps
    # historical workflow records readable while preventing it being exposed
    # again when a draft is re-opened or re-published.
    op.execute("""
        UPDATE ontology.knowledge_governance_item
        SET structured_payload = structured_payload - 'profile_code'
        WHERE form_type = 'center-profile'
          AND structured_payload ? 'profile_code'
    """)

    # The seed compiler owns the canonical hash.  Remove the retired field
    # from the live compiled projection now; the deployment seed command then
    # immediately rebuilds its canonical hash and snapshot metadata.
    op.execute("""
        UPDATE ontology.ontology_compiled_snapshot
        SET compiled_schema = jsonb_set(
                jsonb_set(
                    compiled_schema,
                    '{entityTypes}',
                    COALESCE((
                        SELECT jsonb_agg(
                            CASE
                                WHEN entity->>'technicalName' = 'center_profile'
                                THEN jsonb_set(entity, '{identityStrategy}', '{"required": []}'::jsonb)
                                ELSE entity
                            END
                        )
                        FROM jsonb_array_elements(compiled_schema->'entityTypes') AS entity
                    ), '[]'::jsonb),
                    true
                ),
                '{properties}',
                COALESCE((
                    SELECT jsonb_agg(property)
                    FROM jsonb_array_elements(compiled_schema->'properties') AS property
                    WHERE NOT (
                        property->>'entityType' = 'center_profile'
                        AND property->>'technicalName' = 'profile_code'
                    )
                ), '[]'::jsonb),
                true
            ),
            schema_hash = repeat('0', 64)
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE ontology.ontology_entity_type
        SET identity_strategy = '{"required": ["profile_code"]}'::jsonb
        WHERE technical_name = 'center_profile'
    """)
    op.execute("""
        INSERT INTO ontology.ontology_property (
            id, ontology_version_id, entity_type_id, technical_name,
            display_name, data_type, required, multi_value, constraints, description
        )
        SELECT
            md5(entity.id::text || ':profile_code')::uuid,
            entity.ontology_version_id,
            entity.id,
            'profile_code',
            'Profile Code',
            'STRING'::ontology_property_data_type,
            true,
            false,
            '{}'::jsonb,
            'Mã hồ sơ trung tâm'
        FROM ontology.ontology_entity_type AS entity
        WHERE entity.technical_name = 'center_profile'
          AND NOT EXISTS (
              SELECT 1
              FROM ontology.ontology_property AS property
              WHERE property.entity_type_id = entity.id
                AND property.technical_name = 'profile_code'
          )
    """)
