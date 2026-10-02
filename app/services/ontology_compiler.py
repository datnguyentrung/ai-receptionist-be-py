"""Deterministic compiler from normalized ontology tables to ingestion snapshots."""

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    OntologyAlias,
    OntologyCompiledSnapshot,
    OntologyEntityType,
    OntologyEntityTypeScope,
    OntologyProperty,
    OntologyRelationship,
    OntologyScope,
    OntologyVersion,
)
from app.services.ingestion.ontology import COMPILER_VERSION


class OntologyCompiler:
    """Compiles one or every scope of an immutable ontology version."""

    async def compile_scope(
        self, session: AsyncSession, ontology_version_id: uuid.UUID, scope_key: str
    ) -> OntologyCompiledSnapshot:
        version = await session.get(OntologyVersion, ontology_version_id)
        scope = await session.scalar(
            select(OntologyScope).where(
                OntologyScope.ontology_version_id == ontology_version_id,
                OntologyScope.scope_key == scope_key.strip().casefold(),
            )
        )
        if version is None or scope is None:
            raise KeyError(f"Unknown ontology version/scope: {ontology_version_id}/{scope_key}")

        entity_ids = list(
            (await session.scalars(
                select(OntologyEntityTypeScope.entity_type_id).where(
                    OntologyEntityTypeScope.scope_id == scope.id,
                    OntologyEntityTypeScope.ontology_version_id == ontology_version_id,
                )
            )).all()
        )
        if not entity_ids:
            raise RuntimeError(f"Ontology scope has no entity types: {scope.scope_key}")
        entities = list((await session.scalars(
            select(OntologyEntityType)
            .where(
                OntologyEntityType.ontology_version_id == ontology_version_id,
                OntologyEntityType.id.in_(entity_ids),
            )
            .order_by(OntologyEntityType.technical_name)
        )).all())
        if len(entities) != len(set(entity_ids)):
            raise RuntimeError(f"Scope membership crosses ontology versions: {scope.scope_key}")
        properties = list((await session.scalars(
            select(OntologyProperty)
            .where(
                OntologyProperty.ontology_version_id == ontology_version_id,
                OntologyProperty.entity_type_id.in_(entity_ids),
            )
            .order_by(OntologyProperty.entity_type_id, OntologyProperty.technical_name)
        )).all())
        relationships = list((await session.scalars(
            select(OntologyRelationship)
            .where(
                OntologyRelationship.ontology_version_id == ontology_version_id,
                OntologyRelationship.source_entity_type_id.in_(entity_ids),
                OntologyRelationship.target_entity_type_id.in_(entity_ids),
            )
            .order_by(OntologyRelationship.technical_name)
        )).all())
        property_ids = {item.id for item in properties}
        relationship_ids = {item.id for item in relationships}
        aliases = list((await session.scalars(
            select(OntologyAlias)
            .where(OntologyAlias.ontology_version_id == ontology_version_id)
            .order_by(OntologyAlias.alias)
        )).all())
        aliases = [item for item in aliases if
                   item.entity_type_id in set(entity_ids)
                   or item.property_id in property_ids
                   or item.relationship_id in relationship_ids]
        entity_by_id = {item.id: item for item in entities}

        payload: dict[str, Any] = {
            "versionId": str(version.id),
            "version": version.version,
            "scopeKey": scope.scope_key,
            "scopeKeys": [scope.scope_key],
            "description": scope.description,
            "compilerVersion": COMPILER_VERSION,
            "entityTypes": [
                {
                    "id": str(item.id),
                    "technicalName": item.technical_name,
                    "displayName": item.display_name,
                    "description": item.description,
                    "identityStrategy": item.identity_strategy or {},
                }
                for item in entities
            ],
            "properties": [
                {
                    "id": str(item.id),
                    "entityType": entity_by_id[item.entity_type_id].technical_name,
                    "technicalName": item.technical_name,
                    "displayName": item.display_name,
                    "description": item.description,
                    "dataType": item.data_type.value,
                    "required": item.required,
                    "multiValue": item.multi_value,
                    "constraints": item.constraints or {},
                }
                for item in properties
            ],
            "relationships": [
                {
                    "id": str(item.id),
                    "technicalName": item.technical_name,
                    "displayName": item.display_name,
                    "description": item.description,
                    "sourceEntityType": entity_by_id[item.source_entity_type_id].technical_name,
                    "targetEntityType": entity_by_id[item.target_entity_type_id].technical_name,
                    "cardinality": item.cardinality.value,
                    "constraints": item.constraints or {},
                }
                for item in relationships
            ],
            "aliases": [self._alias_payload(item) for item in aliases],
        }
        hash_payload = {
            key: value for key, value in payload.items()
            if key not in {"versionId", "version"}
        }
        schema_hash = hashlib.sha256(_canonical(hash_payload).encode()).hexdigest()
        snapshot = await session.scalar(select(OntologyCompiledSnapshot).where(
            OntologyCompiledSnapshot.ontology_version_id == ontology_version_id,
            OntologyCompiledSnapshot.scope_key == scope.scope_key,
        ))
        if snapshot is None:
            snapshot = OntologyCompiledSnapshot(
                ontology_version_id=ontology_version_id,
                scope_key=scope.scope_key,
                description=scope.description,
                compiler_version=COMPILER_VERSION,
                schema_hash=schema_hash,
                compiled_schema=payload,
            )
            session.add(snapshot)
        else:
            snapshot.description = scope.description
            snapshot.compiler_version = COMPILER_VERSION
            snapshot.schema_hash = schema_hash
            snapshot.compiled_schema = payload
            snapshot.expires_at = None
        await session.flush()
        return snapshot

    async def compile_all(
        self, session: AsyncSession, ontology_version_id: uuid.UUID
    ) -> list[OntologyCompiledSnapshot]:
        scopes = list((await session.scalars(
            select(OntologyScope)
            .where(OntologyScope.ontology_version_id == ontology_version_id)
            .order_by(OntologyScope.scope_key)
        )).all())
        if not scopes:
            raise RuntimeError(f"Ontology version has no scopes: {ontology_version_id}")
        snapshots = [
            await self.compile_scope(session, ontology_version_id, scope.scope_key)
            for scope in scopes
        ]
        valid_keys = {scope.scope_key for scope in scopes}
        await session.execute(delete(OntologyCompiledSnapshot).where(
            OntologyCompiledSnapshot.ontology_version_id == ontology_version_id,
            OntologyCompiledSnapshot.scope_key.not_in(valid_keys),
        ))
        return snapshots

    @staticmethod
    def _alias_payload(item: OntologyAlias) -> dict[str, Any]:
        target_type, target_id = (
            ("ENTITY_TYPE", item.entity_type_id)
            if item.entity_type_id else
            ("PROPERTY", item.property_id)
            if item.property_id else
            ("RELATIONSHIP", item.relationship_id)
        )
        return {
            "id": str(item.id), "alias": item.alias,
            "targetType": target_type, "targetId": str(target_id),
            "confidence": item.confidence, "source": item.source.value,
        }


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


__all__ = ["OntologyCompiler"]
