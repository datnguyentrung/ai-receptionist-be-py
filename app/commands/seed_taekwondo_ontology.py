"""Seed the initial ACTIVE Taekwondo ontology required by ingestion.

Run with:
    python -m app.commands.seed_taekwondo_ontology
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db.session import AsyncSessionLocal, engine
from app.models import (
    OntologyAlias,
    OntologyAliasSource,
    OntologyEntityType,
    OntologyProperty,
    OntologyPropertyDataType,
    OntologyRelationship,
    OntologyVersion,
    OntologyVersionStatus,
    RelationshipCardinality,
)

VERSION_CODE = "taekwondo-core-v1"

ENTITY_TYPES: tuple[dict[str, Any], ...] = (
    {
        "technical_name": "taekwondo_style",
        "display_name": "Taekwondo style",
        "description": "A recognized Taekwondo style, federation system, or ruleset.",
        "identity_strategy": {"required": ["name"]},
    },
    {
        "technical_name": "organization",
        "display_name": "Organization",
        "description": "A federation, dojang, school, club, or governing body.",
        "identity_strategy": {"required": ["name"]},
    },
    {
        "technical_name": "person",
        "display_name": "Person",
        "description": "A coach, master, student, examiner, or other named person.",
        "identity_strategy": {"required": ["name"]},
    },
    {
        "technical_name": "class_program",
        "display_name": "Class or program",
        "description": "A Taekwondo class, course, training program, or training track.",
        "identity_strategy": {"required": ["name"]},
    },
    {
        "technical_name": "belt_rank",
        "display_name": "Belt rank",
        "description": "A colored belt, kup, poom, dan, or rank level.",
        "identity_strategy": {"required": ["name"]},
    },
    {
        "technical_name": "technique",
        "display_name": "Technique",
        "description": "A stance, block, kick, strike, punch, movement, or poomsae element.",
        "identity_strategy": {"required": ["name"]},
    },
    {
        "technical_name": "poomsae",
        "display_name": "Poomsae",
        "description": "A Taekwondo form or pattern.",
        "identity_strategy": {"required": ["name"]},
    },
    {
        "technical_name": "requirement",
        "display_name": "Requirement",
        "description": "A grading, attendance, age, safety, or curriculum requirement.",
        "identity_strategy": {"required": ["name"]},
    },
    {
        "technical_name": "schedule",
        "display_name": "Schedule",
        "description": "A training time, class slot, event time, or recurring schedule.",
        "identity_strategy": {"required": ["name"]},
    },
    {
        "technical_name": "location",
        "display_name": "Location",
        "description": "A training room, dojang address, venue, or branch location.",
        "identity_strategy": {"required": ["name"]},
    },
    {
        "technical_name": "fee",
        "display_name": "Fee",
        "description": "A tuition, exam fee, membership fee, or equipment cost.",
        "identity_strategy": {"required": ["name"]},
    },
    {
        "technical_name": "policy",
        "display_name": "Policy",
        "description": "A rule, safety policy, uniform policy, or attendance policy.",
        "identity_strategy": {"required": ["name"]},
    },
)

COMMON_PROPERTIES: tuple[tuple[str, OntologyPropertyDataType, bool], ...] = (
    ("name", OntologyPropertyDataType.STRING, True),
    ("description", OntologyPropertyDataType.STRING, False),
    ("notes", OntologyPropertyDataType.STRING, False),
)

ENTITY_PROPERTIES: dict[str, tuple[tuple[str, OntologyPropertyDataType, bool], ...]] = {
    "person": (
        ("role", OntologyPropertyDataType.STRING, False),
        ("phone", OntologyPropertyDataType.STRING, False),
        ("email", OntologyPropertyDataType.STRING, False),
    ),
    "class_program": (
        ("age_range", OntologyPropertyDataType.STRING, False),
        ("level", OntologyPropertyDataType.STRING, False),
        ("duration", OntologyPropertyDataType.STRING, False),
    ),
    "belt_rank": (
        ("rank_order", OntologyPropertyDataType.INTEGER, False),
        ("color", OntologyPropertyDataType.STRING, False),
    ),
    "technique": (
        ("korean_name", OntologyPropertyDataType.STRING, False),
        ("category", OntologyPropertyDataType.STRING, False),
    ),
    "poomsae": (
        ("movement_count", OntologyPropertyDataType.INTEGER, False),
        ("level", OntologyPropertyDataType.STRING, False),
    ),
    "schedule": (
        ("day_of_week", OntologyPropertyDataType.STRING, False),
        ("start_time", OntologyPropertyDataType.STRING, False),
        ("end_time", OntologyPropertyDataType.STRING, False),
    ),
    "location": (
        ("address", OntologyPropertyDataType.STRING, False),
        ("room", OntologyPropertyDataType.STRING, False),
    ),
    "fee": (
        ("amount", OntologyPropertyDataType.FLOAT, False),
        ("currency", OntologyPropertyDataType.STRING, False),
        ("billing_period", OntologyPropertyDataType.STRING, False),
    ),
}

RELATIONSHIPS: tuple[
    tuple[str, str, str, RelationshipCardinality],
    ...,
] = (
    ("organization", "offers_program", "class_program", RelationshipCardinality.ONE_TO_MANY),
    ("organization", "located_at", "location", RelationshipCardinality.MANY_TO_MANY),
    ("person", "teaches_program", "class_program", RelationshipCardinality.MANY_TO_MANY),
    ("person", "holds_rank", "belt_rank", RelationshipCardinality.MANY_TO_MANY),
    ("class_program", "has_schedule", "schedule", RelationshipCardinality.ONE_TO_MANY),
    ("class_program", "uses_location", "location", RelationshipCardinality.MANY_TO_MANY),
    ("class_program", "has_fee", "fee", RelationshipCardinality.ONE_TO_MANY),
    ("class_program", "targets_rank", "belt_rank", RelationshipCardinality.MANY_TO_MANY),
    ("belt_rank", "requires_technique", "technique", RelationshipCardinality.MANY_TO_MANY),
    ("belt_rank", "requires_poomsae", "poomsae", RelationshipCardinality.MANY_TO_MANY),
    ("belt_rank", "has_requirement", "requirement", RelationshipCardinality.ONE_TO_MANY),
    ("class_program", "has_policy", "policy", RelationshipCardinality.ONE_TO_MANY),
    ("taekwondo_style", "defines_poomsae", "poomsae", RelationshipCardinality.ONE_TO_MANY),
    ("taekwondo_style", "defines_rank", "belt_rank", RelationshipCardinality.ONE_TO_MANY),
)

ALIASES: dict[str, tuple[str, ...]] = {
    "class_program": ("class", "program", "course", "lop hoc", "chuong trinh"),
    "person": ("coach", "instructor", "master", "student", "hlv", "huan luyen vien"),
    "belt_rank": ("belt", "rank", "kup", "dan", "dai"),
    "technique": ("kick", "block", "strike", "stance", "ky thuat"),
    "poomsae": ("form", "pattern", "bai quyen"),
    "requirement": ("grading requirement", "exam requirement", "yeu cau"),
    "schedule": ("time", "timetable", "lich hoc"),
    "location": ("venue", "branch", "address", "dia diem"),
    "fee": ("price", "tuition", "hoc phi"),
    "policy": ("rule", "regulation", "quy dinh"),
}


async def seed() -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        active_versions = list(
            (
                await session.scalars(
                    select(OntologyVersion).where(
                        OntologyVersion.status == OntologyVersionStatus.ACTIVE
                    )
                )
            ).all()
        )
        if len(active_versions) == 1:
            return {
                "status": "skipped",
                "reason": "ACTIVE ontology already exists",
                "version": active_versions[0].version,
                "version_id": str(active_versions[0].id),
            }
        if len(active_versions) > 1:
            raise RuntimeError(
                f"Expected at most one ACTIVE ontology version; found {len(active_versions)}"
            )

        version = await session.scalar(
            select(OntologyVersion).where(OntologyVersion.version == VERSION_CODE)
        )
        if version is None:
            version = OntologyVersion(
                version=VERSION_CODE,
                status=OntologyVersionStatus.ACTIVE,
                description="Initial Taekwondo ontology for ingestion and GraphRAG.",
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

        entity_by_name: dict[str, OntologyEntityType] = {}
        for spec in ENTITY_TYPES:
            entity = OntologyEntityType(
                ontology_version_id=version.id,
                technical_name=spec["technical_name"],
                display_name=spec["display_name"],
                description=spec["description"],
                identity_strategy=spec["identity_strategy"],
                metadata_={"scopes": ["core", "taekwondo"]},
            )
            session.add(entity)
            entity_by_name[entity.technical_name] = entity
        await session.flush()

        property_count = 0
        for technical_name, entity in entity_by_name.items():
            specs = (*COMMON_PROPERTIES, *ENTITY_PROPERTIES.get(technical_name, ()))
            for prop_name, data_type, required in specs:
                session.add(
                    OntologyProperty(
                        ontology_version_id=version.id,
                        entity_type_id=entity.id,
                        technical_name=prop_name,
                        display_name=prop_name.replace("_", " ").title(),
                        data_type=data_type,
                        required=required,
                        multi_value=False,
                        constraints={},
                    )
                )
                property_count += 1

        relationship_count = 0
        relationship_by_name: dict[str, OntologyRelationship] = {}
        for source_name, rel_name, target_name, cardinality in RELATIONSHIPS:
            relationship = OntologyRelationship(
                ontology_version_id=version.id,
                technical_name=rel_name,
                display_name=rel_name.replace("_", " ").title(),
                source_entity_type_id=entity_by_name[source_name].id,
                target_entity_type_id=entity_by_name[target_name].id,
                cardinality=cardinality,
                constraints={},
            )
            session.add(relationship)
            relationship_by_name[rel_name] = relationship
            relationship_count += 1
        await session.flush()

        alias_count = 0
        for entity_name, aliases in ALIASES.items():
            for alias in aliases:
                session.add(
                    OntologyAlias(
                        ontology_version_id=version.id,
                        alias=alias,
                        entity_type_id=entity_by_name[entity_name].id,
                        confidence=1.0,
                        source=OntologyAliasSource.MANUAL,
                    )
                )
                alias_count += 1
        for relationship_name, relationship in relationship_by_name.items():
            session.add(
                OntologyAlias(
                    ontology_version_id=version.id,
                    alias=relationship_name.replace("_", " "),
                    relationship_id=relationship.id,
                    confidence=1.0,
                    source=OntologyAliasSource.MANUAL,
                )
            )
            alias_count += 1

        await session.commit()
        return {
            "status": "seeded",
            "version": version.version,
            "version_id": str(version.id),
            "entity_types": len(entity_by_name),
            "properties": property_count,
            "relationships": relationship_count,
            "aliases": alias_count,
        }


async def _main() -> None:
    try:
        print(await seed())
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
