"""Deterministic compiler from normalized ontology tables to snapshots."""

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

COMPILER_VERSION = "ontology-compiler-v2"


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
            .order_by(
                OntologyRelationship.source_entity_type_id,
                OntologyRelationship.technical_name,
                OntologyRelationship.target_entity_type_id,
            )
        )).all())
        alias_items = list((await session.scalars(
            select(OntologyAlias)
            .where(
                OntologyAlias.ontology_version_id == ontology_version_id,
                (
                    OntologyAlias.entity_type_id.in_(entity_ids)
                    | OntologyAlias.property_id.in_([p.id for p in properties])
                    | OntologyAlias.relationship_id.in_([r.id for r in relationships])
                ),
            )
            .order_by(OntologyAlias.alias)
        )).all())

        entity_name_by_id = {str(e.id): e.technical_name for e in entities}

        entities_payload = [
            {
                "id": str(e.id),
                "technicalName": e.technical_name,
                "displayName": e.display_name or e.technical_name,
                "domain": str((e.metadata_ or {}).get("domain", "")),
                "identityStrategy": e.identity_strategy,
                "allowDynamicProperties": bool((e.metadata_ or {}).get("allow_dynamic_properties", False)),
                "description": e.description,
            }
            for e in entities
        ]

        # 4. Danh sách thuộc tính phẳng (list[dict]) có kèm trường entityType
        props_payload = [
            {
                "id": str(p.id),
                "entityType": entity_name_by_id.get(str(p.entity_type_id), ""),
                "technicalName": p.technical_name,
                "displayName": p.display_name or p.technical_name,
                "dataType": p.data_type.value,
                "required": p.required,
                "multiValue": p.multi_value,
                "constraints": p.constraints or {},
                "description": p.description,
            }
            for p in properties
        ]

        # 5. Danh sách quan hệ có kèm sourceEntityType và targetEntityType
        rel_payload = [
            {
                "id": str(r.id),
                "technicalName": r.technical_name,
                "displayName": r.display_name or r.technical_name,
                "sourceEntityType": entity_name_by_id.get(str(r.source_entity_type_id), ""),
                "targetEntityType": entity_name_by_id.get(str(r.target_entity_type_id), ""),
                "cardinality": r.cardinality.value,
                "constraints": r.constraints or {},
                "description": r.description,
            }
            for r in relationships
        ]

        aliases_payload = [self._alias_payload(a) for a in alias_items]
        payload = {
            "scopeKey": scope.scope_key,
            "versionId": str(version.id),
            "version": version.version,
            "description": scope.description or "",
            "compilerVersion": COMPILER_VERSION,
            "entityTypes": entities_payload,
            "properties": props_payload,
            "relationships": rel_payload,
            "aliases": aliases_payload,
        }
        schema_hash = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        snapshot = await session.scalar(
            select(OntologyCompiledSnapshot).where(
                OntologyCompiledSnapshot.ontology_version_id == ontology_version_id,
                OntologyCompiledSnapshot.scope_key == scope.scope_key,
            )
        )
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


__all__ = ["COMPILER_VERSION", "OntologyCompiler"]
