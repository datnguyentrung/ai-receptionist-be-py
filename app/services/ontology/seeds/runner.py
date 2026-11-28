"""Versioned, idempotent seed runner for Layer 1 & Layer 2 Ontology."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import (
    OntologyAlias,
    OntologyAliasSource,
    OntologyEntityType,
    OntologyEntityTypeScope,
    OntologyProperty,
    OntologyRelationship,
    OntologyScope,
    OntologyVersion,
    OntologyVersionStatus,
)
from app.services.ontology.ontology_compiler import OntologyCompiler
from app.services.ontology.seeds.layer1_business import (
    LAYER_1_ALIASES,
    LAYER_1_ENTITY_TYPES,
    LAYER_1_PROPERTIES,
    LAYER_1_RELATIONSHIPS,
    get_layer1_entity_governance,
    get_layer1_property_governance,
    get_layer1_relationship_governance,
)
from app.services.ontology.seeds.layer2_core import (
    LAYER_2_ALIASES,
    LAYER_2_ENTITY_TYPES,
    LAYER_2_PROPERTIES,
    LAYER_2_RELATIONSHIPS,
    LAYER_2_SCOPE_SPECS,
    get_layer2_entity_governance,
    get_layer2_property_governance,
    get_layer2_relationship_governance,
)

ONTOLOGY_VERSION_CODE = "taekwondo-core-v2"


async def _get_or_create_active_version(
    session: AsyncSession, version_code: str
) -> tuple[OntologyVersion, bool]:
    active_versions = list(
        (
            await session.scalars(
                select(OntologyVersion).where(
                    OntologyVersion.status == OntologyVersionStatus.ACTIVE
                )
            )
        ).all()
    )
    if len(active_versions) > 1:
        raise RuntimeError(
            f"Expected at most one ACTIVE ontology version; found {len(active_versions)}"
        )
    if len(active_versions) == 1:
        return active_versions[0], False

    version = await session.scalar(
        select(OntologyVersion).where(OntologyVersion.version == version_code)
    )
    if version is None:
        version = OntologyVersion(
            version=version_code,
            status=OntologyVersionStatus.ACTIVE,
            description="Layer 1 Business + Layer 2 Core Knowledge Ontology for CLEF Multi-Agent.",
            created_by="seed_taekwondo_ontology",
            activated_at=datetime.now(UTC),
        )
        session.add(version)
        await session.flush()
    else:
        version.status = OntologyVersionStatus.ACTIVE
        version.activated_at = datetime.now(UTC)
        session.add(version)
        await session.flush()
    return version, True


async def seed_ontology_in_session(
    session: AsyncSession, version_code: str = ONTOLOGY_VERSION_CODE
) -> dict[str, Any]:
    """Idempotently ensure Layer 1 & Layer 2 definitions exist in PostgreSQL.

    Additive-only: never overwrites existing active entities, properties, or
    relationships that an admin may have customized, and never creates duplicates.
    """
    version, created_version = await _get_or_create_active_version(session, version_code)

    existing_entities = list(
        (
            await session.scalars(
                select(OntologyEntityType).where(
                    OntologyEntityType.ontology_version_id == version.id
                )
            )
        ).all()
    )
    entity_by_name = {item.technical_name: item for item in existing_entities}

    entities_added = 0
    l1_names = {spec["technical_name"] for spec in LAYER_1_ENTITY_TYPES}
    for spec in (*LAYER_1_ENTITY_TYPES, *LAYER_2_ENTITY_TYPES):
        name = spec["technical_name"]
        gov = (
            get_layer1_entity_governance(spec)
            if name in l1_names
            else get_layer2_entity_governance(spec)
        )
        enriched_meta = {**spec["metadata"], **gov.to_compiled_dict()}
        if name in entity_by_name:
            existing_entity = entity_by_name[name]
            if "sourceOfTruth" not in (existing_entity.metadata_ or {}):
                existing_entity.metadata_ = enriched_meta
            continue
        entity = OntologyEntityType(
            ontology_version_id=version.id,
            technical_name=name,
            display_name=spec["display_name"],
            description=spec["description"],
            identity_strategy=spec["identity_strategy"],
            metadata_=enriched_meta,
        )
        session.add(entity)
        await session.flush()
        entity_by_name[name] = entity
        entities_added += 1

    properties_added = 0
    all_properties = {**LAYER_1_PROPERTIES, **LAYER_2_PROPERTIES}
    for entity_name, prop_specs in all_properties.items():
        entity = entity_by_name.get(entity_name)
        if entity is None:
            continue
        existing_props = {
            prop.technical_name: prop
            for prop in (
                await session.scalars(
                    select(OntologyProperty).where(
                        OntologyProperty.entity_type_id == entity.id
                    )
                )
            ).all()
        }
        for (
            prop_name,
            display_name,
            data_type,
            required,
            multi_value,
            constraints,
            description,
        ) in prop_specs:
            prop_gov = (
                get_layer1_property_governance(entity_name, prop_name)
                if entity_name in LAYER_1_PROPERTIES
                else get_layer2_property_governance(entity_name, prop_name)
            )
            merged_constraints = {
                **(constraints or {}),
                "governance": prop_gov.to_compiled_dict(),
            }
            if prop_name in existing_props:
                existing_prop = existing_props[prop_name]
                if "governance" not in (existing_prop.constraints or {}):
                    existing_prop.constraints = merged_constraints
                continue
            session.add(
                OntologyProperty(
                    ontology_version_id=version.id,
                    entity_type_id=entity.id,
                    technical_name=prop_name,
                    display_name=display_name,
                    data_type=data_type,
                    required=required,
                    multi_value=multi_value,
                    constraints=merged_constraints,
                    description=description,
                )
            )
            properties_added += 1
    await session.flush()

    existing_rels = {
        (rel.technical_name, rel.source_entity_type_id, rel.target_entity_type_id): rel
        for rel in (
            await session.scalars(
                select(OntologyRelationship).where(
                    OntologyRelationship.ontology_version_id == version.id
                )
            )
        ).all()
    }
    relationships_added = 0
    relationship_by_name: dict[str, OntologyRelationship] = {}
    l1_rel_keys = {(s, r, t) for s, r, t, *_ in LAYER_1_RELATIONSHIPS}
    for source_name, rel_name, target_name, cardinality, description in (
        *LAYER_1_RELATIONSHIPS,
        *LAYER_2_RELATIONSHIPS,
    ):
        source = entity_by_name.get(source_name)
        target = entity_by_name.get(target_name)
        if source is None or target is None:
            continue
        rel_gov = (
            get_layer1_relationship_governance(source_name, rel_name, target_name)
            if (source_name, rel_name, target_name) in l1_rel_keys
            else get_layer2_relationship_governance(source_name, rel_name, target_name)
        )
        rel_constraints = {"governance": rel_gov.to_compiled_dict()}
        key = (rel_name, source.id, target.id)
        if key in existing_rels:
            existing_rel = existing_rels[key]
            if "governance" not in (existing_rel.constraints or {}):
                existing_rel.constraints = {**(existing_rel.constraints or {}), **rel_constraints}
            continue
        rel = OntologyRelationship(
            ontology_version_id=version.id,
            technical_name=rel_name,
            display_name=rel_name.replace("_", " ").title(),
            source_entity_type_id=source.id,
            target_entity_type_id=target.id,
            cardinality=cardinality,
            constraints=rel_constraints,
            description=description,
        )
        session.add(rel)
        existing_rels[key] = rel
        relationship_by_name[rel_name] = rel
        relationships_added += 1
    await session.flush()

    existing_aliases = {
        (item.alias.casefold(), item.entity_type_id, item.relationship_id)
        for item in (
            await session.scalars(
                select(OntologyAlias).where(
                    OntologyAlias.ontology_version_id == version.id
                )
            )
        ).all()
    }
    aliases_added = 0
    all_aliases = {**LAYER_1_ALIASES, **LAYER_2_ALIASES}
    for entity_name, aliases in all_aliases.items():
        entity = entity_by_name.get(entity_name)
        if entity is None:
            continue
        for alias in aliases:
            key = (alias.casefold(), entity.id, None)
            if key in existing_aliases:
                continue
            session.add(
                OntologyAlias(
                    ontology_version_id=version.id,
                    alias=alias,
                    entity_type_id=entity.id,
                    confidence=1.0,
                    source=OntologyAliasSource.MANUAL,
                )
            )
            existing_aliases.add(key)
            aliases_added += 1

    scopes_added = 0
    memberships_added = 0
    for scope_key, (description, entity_names) in LAYER_2_SCOPE_SPECS.items():
        scope = await session.scalar(
            select(OntologyScope).where(
                OntologyScope.ontology_version_id == version.id,
                OntologyScope.scope_key == scope_key,
            )
        )
        if scope is None:
            scope = OntologyScope(
                ontology_version_id=version.id,
                scope_key=scope_key,
                description=description,
                summary={"entityTypeCount": len(entity_names)},
            )
            session.add(scope)
            await session.flush()
            scopes_added += 1
        for name in entity_names:
            entity = entity_by_name.get(name)
            if entity is None:
                continue
            membership = await session.get(
                OntologyEntityTypeScope, (scope.id, entity.id)
            )
            if membership is None:
                session.add(
                    OntologyEntityTypeScope(
                        ontology_version_id=version.id,
                        scope_id=scope.id,
                        entity_type_id=entity.id,
                    )
                )
                memberships_added += 1
    await session.flush()

    snapshots = await OntologyCompiler().compile_all(session, version.id)
    return {
        "status": "seeded" if created_version else "ensured",
        "version": version.version,
        "version_id": str(version.id),
        "entities_added": entities_added,
        "properties_added": properties_added,
        "relationships_added": relationships_added,
        "aliases_added": aliases_added,
        "scopes_added": scopes_added,
        "scope_memberships_added": memberships_added,
        "snapshots": len(snapshots),
    }


async def seed_ontology(version_code: str = ONTOLOGY_VERSION_CODE) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        result = await seed_ontology_in_session(session, version_code=version_code)
        await session.commit()
        return result


__all__ = ["ONTOLOGY_VERSION_CODE", "seed_ontology", "seed_ontology_in_session"]
