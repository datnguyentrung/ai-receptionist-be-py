"""Human-approved ontology evolution and snapshot publication."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    OntologyAlias,
    OntologyAliasSource,
    OntologyEntityType,
    OntologyEntityTypeScope,
    OntologyProperty,
    OntologyPropertyDataType,
    OntologyRelationship,
    OntologySchemaProposal,
    OntologyScope,
    OntologyVersion,
    OntologyVersionStatus,
    RelationshipCardinality,
    SchemaProposalStatus,
    SchemaProposalType,
)
from app.services.ingestion.ontology import OntologyCache
from app.services.ontology_compiler import OntologyCompiler


class OntologyLifecycle:
    """Enforces proposal transitions and publishes complete ontology versions."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        compiler: OntologyCompiler,
        cache: OntologyCache,
    ) -> None:
        self._session_factory = session_factory
        self._compiler = compiler
        self._cache = cache

    async def create_proposal(
        self,
        *,
        ingestion_id: str,
        batch_index: int,
        ontology_version_id: str,
        source_document_id: str | None,
        proposal_type: str,
        technical_name: str | None,
        reason: str,
        payload: dict[str, Any],
        evidence: dict[str, Any],
        affected_scope_keys: list[str],
    ) -> OntologySchemaProposal:
        proposal_kind = SchemaProposalType(proposal_type)
        digest = _digest({
            "ingestionId": ingestion_id, "batchIndex": batch_index,
            "type": proposal_kind.value, "technicalName": technical_name,
            "payload": payload, "evidence": evidence,
        })
        async with self._session_factory() as session, session.begin():
            existing = await session.scalar(select(OntologySchemaProposal).where(
                OntologySchemaProposal.proposal_digest == digest
            ))
            if existing is not None:
                return existing
            proposal = OntologySchemaProposal(
                ontology_version_id=uuid.UUID(ontology_version_id),
                proposal_type=proposal_kind,
                status=SchemaProposalStatus.PROPOSED,
                technical_name=technical_name,
                reason=reason,
                payload=payload,
                evidence=evidence,
                source_document_id=source_document_id,
                source_ingestion_id=ingestion_id,
                source_batch_index=batch_index,
                affected_scope_keys=list(dict.fromkeys(affected_scope_keys)),
                proposal_digest=digest,
            )
            session.add(proposal)
            await session.flush()
            return proposal

    async def review_proposal(
        self, proposal_id: str, *, approved: bool, reviewed_by: str
    ) -> OntologySchemaProposal:
        if not reviewed_by.strip():
            raise ValueError("reviewed_by is required")
        async with self._session_factory() as session, session.begin():
            proposal = await session.scalar(select(OntologySchemaProposal).where(
                OntologySchemaProposal.id == uuid.UUID(proposal_id)
            ).with_for_update())
            if proposal is None:
                raise KeyError(proposal_id)
            if proposal.status != SchemaProposalStatus.PROPOSED:
                raise ValueError(f"Proposal is already {proposal.status.value}")
            proposal.status = proposal_review_status(proposal.status, approved)
            proposal.reviewed_by = reviewed_by
            proposal.reviewed_at = datetime.now(UTC)
            return proposal

    async def apply_proposal(
        self, proposal_id: str, *, new_version_code: str, applied_by: str
    ) -> OntologyVersion:
        async with self._session_factory() as session, session.begin():
            proposal = await session.scalar(select(OntologySchemaProposal).where(
                OntologySchemaProposal.id == uuid.UUID(proposal_id)
            ).with_for_update())
            if proposal is None:
                raise KeyError(proposal_id)
            if proposal.status == SchemaProposalStatus.APPLIED and proposal.applied_ontology_version_id:
                return await session.get(OntologyVersion, proposal.applied_ontology_version_id)
            if proposal.status != SchemaProposalStatus.APPROVED:
                raise ValueError("Only an APPROVED proposal can be applied")
            if await session.scalar(select(OntologyVersion).where(OntologyVersion.version == new_version_code)):
                raise ValueError(f"Ontology version already exists: {new_version_code}")
            active = await session.scalar(
                select(OntologyVersion)
                .where(OntologyVersion.status == OntologyVersionStatus.ACTIVE)
                .with_for_update()
            )
            if active is None:
                raise RuntimeError("No active ontology version")
            # A proposal records the version on which it was discovered, but other
            # approved proposals may have been applied since then.  Always branch
            # from the currently active version so sequential approvals do not
            # resurrect an archived schema.  Name/uniqueness checks in _apply_change
            # make an incompatible stale proposal fail explicitly.
            target, maps = await self._clone_version(
                session, active.id, new_version_code, applied_by
            )
            await self._apply_change(session, proposal, target.id, maps)
            await self._compiler.compile_all(session, target.id)
            active.status = OntologyVersionStatus.ARCHIVED
            await session.flush()
            target.status = OntologyVersionStatus.ACTIVE
            target.activated_at = datetime.now(UTC)
            proposal.status = SchemaProposalStatus.APPLIED
            proposal.applied_ontology_version_id = target.id
            await session.flush()
        await self._cache.refresh()
        return target

    async def get_proposal(self, proposal_id: str) -> OntologySchemaProposal | None:
        async with self._session_factory() as session:
            return await session.get(OntologySchemaProposal, uuid.UUID(proposal_id))

    async def _clone_version(
        self, session: AsyncSession, source_id: uuid.UUID, version_code: str, created_by: str
    ) -> tuple[OntologyVersion, dict[str, dict[uuid.UUID, Any]]]:
        source = await session.get(OntologyVersion, source_id)
        if source is None:
            raise KeyError(source_id)
        target = OntologyVersion(
            version=version_code, status=OntologyVersionStatus.DRAFT,
            parent_version_id=source.id,
            description=f"Derived from {source.version}", created_by=created_by,
        )
        session.add(target)
        await session.flush()
        entity_map: dict[uuid.UUID, OntologyEntityType] = {}
        for item in (await session.scalars(select(OntologyEntityType).where(
            OntologyEntityType.ontology_version_id == source_id
        ))).all():
            clone = OntologyEntityType(
                ontology_version_id=target.id, technical_name=item.technical_name,
                display_name=item.display_name, description=item.description,
                identity_strategy=item.identity_strategy, metadata_=item.metadata_,
            )
            session.add(clone)
            entity_map[item.id] = clone
        await session.flush()
        property_map: dict[uuid.UUID, OntologyProperty] = {}
        for item in (await session.scalars(select(OntologyProperty).where(
            OntologyProperty.ontology_version_id == source_id
        ))).all():
            clone = OntologyProperty(
                ontology_version_id=target.id, entity_type_id=entity_map[item.entity_type_id].id,
                technical_name=item.technical_name, display_name=item.display_name,
                data_type=item.data_type, required=item.required, multi_value=item.multi_value,
                constraints=item.constraints, description=item.description,
            )
            session.add(clone)
            property_map[item.id] = clone
        relationship_map: dict[uuid.UUID, OntologyRelationship] = {}
        for item in (await session.scalars(select(OntologyRelationship).where(
            OntologyRelationship.ontology_version_id == source_id
        ))).all():
            clone = OntologyRelationship(
                ontology_version_id=target.id, technical_name=item.technical_name,
                display_name=item.display_name,
                source_entity_type_id=entity_map[item.source_entity_type_id].id,
                target_entity_type_id=entity_map[item.target_entity_type_id].id,
                cardinality=item.cardinality, description=item.description,
                constraints=item.constraints,
            )
            session.add(clone)
            relationship_map[item.id] = clone
        await session.flush()
        scope_map: dict[uuid.UUID, OntologyScope] = {}
        scopes = list((await session.scalars(select(OntologyScope).where(
            OntologyScope.ontology_version_id == source_id
        ))).all())
        for item in scopes:
            clone = OntologyScope(
                ontology_version_id=target.id, scope_key=item.scope_key,
                description=item.description, summary=item.summary or {},
            )
            session.add(clone)
            scope_map[item.id] = clone
        await session.flush()
        memberships = list((await session.scalars(select(OntologyEntityTypeScope).where(
            OntologyEntityTypeScope.scope_id.in_([item.id for item in scopes])
        ))).all()) if scopes else []
        session.add_all([
            OntologyEntityTypeScope(
                ontology_version_id=target.id,
                scope_id=scope_map[item.scope_id].id,
                entity_type_id=entity_map[item.entity_type_id].id,
            ) for item in memberships
        ])
        for item in (await session.scalars(select(OntologyAlias).where(
            OntologyAlias.ontology_version_id == source_id
        ))).all():
            session.add(OntologyAlias(
                ontology_version_id=target.id, alias=item.alias,
                entity_type_id=entity_map[item.entity_type_id].id if item.entity_type_id else None,
                property_id=property_map[item.property_id].id if item.property_id else None,
                relationship_id=relationship_map[item.relationship_id].id if item.relationship_id else None,
                confidence=item.confidence, source=item.source,
            ))
        await session.flush()
        return target, {"entities": entity_map, "properties": property_map, "relationships": relationship_map, "scopes": scope_map}

    async def _apply_change(
        self, session: AsyncSession, proposal: OntologySchemaProposal,
        version_id: uuid.UUID, maps: dict[str, dict[uuid.UUID, Any]],
    ) -> None:
        payload = proposal.payload
        kind = proposal.proposal_type
        if kind == SchemaProposalType.NEW_SCOPE:
            scope = OntologyScope(
                ontology_version_id=version_id,
                scope_key=payload["scopeKey"].strip().casefold(),
                description=payload["description"], summary=payload.get("summary", {}),
            )
            session.add(scope)
            await session.flush()
            entities = await self._entities_by_names(session, version_id, payload.get("entityTypeTechnicalNames", []))
            session.add_all([
                OntologyEntityTypeScope(
                    ontology_version_id=version_id,
                    scope_id=scope.id,
                    entity_type_id=item.id,
                ) for item in entities
            ])
            return
        if kind == SchemaProposalType.MODIFY_SCOPE:
            scope = await self._scope_by_key(session, version_id, payload["scopeKey"])
            if "description" in payload:
                scope.description = payload["description"]
            entities = await self._entities_by_names(session, version_id, payload.get("addEntityTypeTechnicalNames", []))
            for item in entities:
                exists = await session.get(OntologyEntityTypeScope, (scope.id, item.id))
                if not exists:
                    session.add(OntologyEntityTypeScope(
                        ontology_version_id=version_id,
                        scope_id=scope.id,
                        entity_type_id=item.id,
                    ))
            removed = await self._entities_by_names(
                session, version_id, payload.get("removeEntityTypeTechnicalNames", [])
            )
            if removed:
                await session.execute(delete(OntologyEntityTypeScope).where(
                    OntologyEntityTypeScope.scope_id == scope.id,
                    OntologyEntityTypeScope.entity_type_id.in_([item.id for item in removed]),
                ))
            return
        if kind == SchemaProposalType.NEW_ENTITY_TYPE:
            entity = OntologyEntityType(
                ontology_version_id=version_id, technical_name=payload["technicalName"],
                display_name=payload.get("displayName", payload["technicalName"]),
                description=payload.get("description"),
                identity_strategy=payload.get("identityStrategy", {}), metadata_={},
            )
            session.add(entity)
            await session.flush()
            for key in payload.get("scopeKeys", proposal.affected_scope_keys):
                scope = await self._scope_by_key(session, version_id, key)
                session.add(OntologyEntityTypeScope(
                    ontology_version_id=version_id,
                    scope_id=scope.id,
                    entity_type_id=entity.id,
                ))
            return
        if kind == SchemaProposalType.NEW_PROPERTY:
            entity = (await self._entities_by_names(session, version_id, [payload["entityType"]]))[0]
            session.add(OntologyProperty(
                ontology_version_id=version_id, entity_type_id=entity.id,
                technical_name=payload["technicalName"],
                display_name=payload.get("displayName", payload["technicalName"]),
                description=payload.get("description"),
                data_type=OntologyPropertyDataType(payload["dataType"]),
                required=payload.get("required", False), multi_value=payload.get("multiValue", False),
                constraints=payload.get("constraints", {}),
            ))
            return
        if kind == SchemaProposalType.NEW_RELATIONSHIP:
            source, target = await self._entities_by_names(
                session, version_id, [payload["sourceEntityType"], payload["targetEntityType"]]
            )
            session.add(OntologyRelationship(
                ontology_version_id=version_id, technical_name=payload["technicalName"],
                display_name=payload.get("displayName", payload["technicalName"]),
                description=payload.get("description"), source_entity_type_id=source.id,
                target_entity_type_id=target.id,
                cardinality=RelationshipCardinality(payload.get("cardinality", "MANY_TO_MANY")),
                constraints=payload.get("constraints", {}),
            ))
            return
        if kind == SchemaProposalType.NEW_ALIAS:
            target_type = payload["targetType"]
            kwargs: dict[str, Any] = {}
            if target_type == "ENTITY_TYPE":
                kwargs["entity_type_id"] = (await self._entities_by_names(session, version_id, [payload["targetTechnicalName"]]))[0].id
            elif target_type == "PROPERTY":
                entity = (await self._entities_by_names(session, version_id, [payload["entityType"]]))[0]
                prop = await session.scalar(select(OntologyProperty).where(
                    OntologyProperty.ontology_version_id == version_id,
                    OntologyProperty.entity_type_id == entity.id,
                    OntologyProperty.technical_name == payload["targetTechnicalName"],
                ))
                if prop is None:
                    raise KeyError(payload["targetTechnicalName"])
                kwargs["property_id"] = prop.id
            elif target_type == "RELATIONSHIP":
                relationship = await session.scalar(select(OntologyRelationship).where(
                    OntologyRelationship.ontology_version_id == version_id,
                    OntologyRelationship.technical_name == payload["targetTechnicalName"],
                ))
                if relationship is None:
                    raise KeyError(payload["targetTechnicalName"])
                kwargs["relationship_id"] = relationship.id
            else:
                raise ValueError(f"Unsupported alias targetType: {target_type}")
            session.add(OntologyAlias(
                ontology_version_id=version_id, alias=payload["alias"],
                confidence=payload.get("confidence", 1.0), source=OntologyAliasSource.INGESTION,
                **kwargs,
            ))
            return
        if kind == SchemaProposalType.MODIFY_ENTITY_TYPE:
            entity = (await self._entities_by_names(
                session, version_id, [payload["technicalName"]]
            ))[0]
            for field in ("displayName", "description", "identityStrategy"):
                if field in payload:
                    setattr(entity, {
                        "displayName": "display_name",
                        "description": "description",
                        "identityStrategy": "identity_strategy",
                    }[field], payload[field])
            return
        if kind == SchemaProposalType.MODIFY_PROPERTY:
            entity = (await self._entities_by_names(
                session, version_id, [payload["entityType"]]
            ))[0]
            prop = await session.scalar(select(OntologyProperty).where(
                OntologyProperty.ontology_version_id == version_id,
                OntologyProperty.entity_type_id == entity.id,
                OntologyProperty.technical_name == payload["technicalName"],
            ))
            if prop is None:
                raise KeyError(payload["technicalName"])
            mapping = {
                "displayName": "display_name", "description": "description",
                "required": "required", "multiValue": "multi_value",
                "constraints": "constraints",
            }
            for source, target in mapping.items():
                if source in payload:
                    setattr(prop, target, payload[source])
            if "dataType" in payload:
                prop.data_type = OntologyPropertyDataType(payload["dataType"])
            return
        if kind == SchemaProposalType.MODIFY_RELATIONSHIP:
            relationship = await session.scalar(select(OntologyRelationship).where(
                OntologyRelationship.ontology_version_id == version_id,
                OntologyRelationship.technical_name == payload["technicalName"],
            ))
            if relationship is None:
                raise KeyError(payload["technicalName"])
            mapping = {
                "displayName": "display_name", "description": "description",
                "constraints": "constraints",
            }
            for source, target in mapping.items():
                if source in payload:
                    setattr(relationship, target, payload[source])
            if "cardinality" in payload:
                relationship.cardinality = RelationshipCardinality(payload["cardinality"])
            return
        raise ValueError(f"Proposal type is not implemented safely: {kind.value}")

    @staticmethod
    async def _entities_by_names(session: AsyncSession, version_id: uuid.UUID, names: list[str]):
        if not names:
            return []
        rows = list((await session.scalars(select(OntologyEntityType).where(
            OntologyEntityType.ontology_version_id == version_id,
            OntologyEntityType.technical_name.in_(names),
        ))).all())
        by_name = {item.technical_name: item for item in rows}
        missing = [name for name in names if name not in by_name]
        if missing:
            raise KeyError(f"Unknown entity types: {missing}")
        return [by_name[name] for name in names]

    @staticmethod
    async def _scope_by_key(session: AsyncSession, version_id: uuid.UUID, key: str):
        scope = await session.scalar(select(OntologyScope).where(
            OntologyScope.ontology_version_id == version_id,
            OntologyScope.scope_key == key.strip().casefold(),
        ))
        if scope is None:
            raise KeyError(f"Unknown ontology scope: {key}")
        return scope


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def proposal_review_status(
    current: SchemaProposalStatus, approved: bool
) -> SchemaProposalStatus:
    if current != SchemaProposalStatus.PROPOSED:
        raise ValueError(f"Proposal is already {current.value}")
    return SchemaProposalStatus.APPROVED if approved else SchemaProposalStatus.REJECTED


__all__ = ["OntologyLifecycle", "proposal_review_status"]
