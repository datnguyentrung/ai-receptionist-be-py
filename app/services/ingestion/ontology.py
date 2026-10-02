"""Ontology loading, projection, and deterministic graph-patch validation."""

import hashlib
import json
import unicodedata
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    OntologyAlias,
    OntologyEntityType,
    OntologyProperty,
    OntologyRelationship,
    OntologyVersion,
    OntologyVersionStatus,
)
from app.schemas.ingestion_schema import (
    GraphPatchFragment,
    OntologyProjection,
    PreparedChunk,
    ValidationIssue,
)

ALLOWED_SCOPES = frozenset(
    {"core", "course", "training", "belt", "facility", "finance", "event"}
)
COMPILER_VERSION = "taekwondo-pg-v1"


class OntologyRegistry:
    """Bộ kiểm tra xác thực đối chiếu đồ thị tri thức với Schema Ontology.

    Nhiệm vụ:
        - Đối chiếu các Thực thể (`nodes`), Thuộc tính (`properties`) và Quan hệ (`edges`)
          trong `GraphPatchFragment` với phiên bản Ontology đang hoạt động (`ACTIVE`).
        - Kiểm tra tính xác thực của các trích dẫn bằng chứng (`evidence`) có trong đoạn văn bản nguồn hay không.
    """

    def __init__(self, projection: OntologyProjection) -> None:
        self.projection = projection
        self.entity_types = {
            item["technicalName"]: item for item in projection.entity_types
        }
        self.properties = {
            (item["entityType"], item["technicalName"]): item
            for item in projection.properties
        }
        self.relationships = {
            item["technicalName"]: item for item in projection.relationships
        }

    def validate_fragment(
        self,
        fragment: GraphPatchFragment,
        chunks: Iterable[PreparedChunk],
    ) -> list[ValidationIssue]:
        """Xác thực toàn diện mảnh đồ thị (GraphPatchFragment) đối chiếu với các chunks văn bản.

        Kiểm tra:
            1. Phiên bản ontology (`ontology_version`) phải khớp với phiên bản đang nạp.
            2. Độ phủ (`coverage`): Mọi chunk trong batch phải có quyết định MAPPED hoặc NOT_RELEVANT.
            3. Tính hợp lệ của từng Nút (Node): Loại thực thể tồn tại, trường định danh hợp lệ.
            4. Thuộc tính (Property): Tên thuộc tính, kiểu dữ liệu, các ràng buộc và bằng chứng trích xuất.
            5. Mối quan hệ (Edge): Nút nguồn, nút đích, tên quan hệ hợp lệ và bằng chứng liên kết.
        """
        issues: list[ValidationIssue] = []
        chunk_by_index = {chunk.chunk_index: chunk for chunk in chunks}

        # 1. Kiểm tra phiên bản Ontology
        if fragment.ontology_version != self.projection.version:
            issues.append(
                ValidationIssue(
                    code="ONTOLOGY_VERSION_MISMATCH",
                    message="Fragment ontologyVersion does not match the job-pinned version",
                    location="ontologyVersion",
                )
            )

        supplied_coverage = {item.chunk_index for item in fragment.coverage}
        expected_coverage = set(chunk_by_index)
        for index in sorted(expected_coverage - supplied_coverage):
            issues.append(
                ValidationIssue(
                    code="COVERAGE_MISSING",
                    message=f"Chunk {index} is missing from coverage",
                    location="coverage",
                    retryable=True,
                )
            )
        for index in sorted(supplied_coverage - expected_coverage):
            issues.append(
                ValidationIssue(
                    code="COVERAGE_UNKNOWN_CHUNK",
                    message=f"Coverage references unknown chunk {index}",
                    location="coverage",
                )
            )

        node_types: dict[str, str] = {}
        for node_index, node in enumerate(fragment.nodes):
            node_types[node.temp_id] = node.class_name
            if node.class_name not in self.entity_types:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_ENTITY_TYPE",
                        message=f"Unknown entity type: {node.class_name}",
                        location=f"nodes.{node_index}.className",
                    )
                )
            issues.extend(
                self._validate_evidence(
                    node.evidence, chunk_by_index, f"nodes.{node_index}.evidence"
                )
            )
            for property_index, fact in enumerate(node.properties):
                contract = self.properties.get((node.class_name, fact.property_name))
                if contract is None:
                    issues.append(
                        ValidationIssue(
                            code="UNKNOWN_PROPERTY",
                            message=f"Property {fact.property_name} is not valid for {node.class_name}",
                            location=f"nodes.{node_index}.properties.{property_index}",
                        )
                    )
                elif not _matches_datatype(fact.value, contract["dataType"]):
                    issues.append(
                        ValidationIssue(
                            code="PROPERTY_DATATYPE_MISMATCH",
                            message=f"Property {fact.property_name} expects {contract['dataType']}",
                            location=f"nodes.{node_index}.properties.{property_index}.value",
                        )
                    )
                issues.extend(
                    self._validate_evidence(
                        fact.evidence,
                        chunk_by_index,
                        f"nodes.{node_index}.properties.{property_index}.evidence",
                    )
                )

        for edge_index, edge in enumerate(fragment.edges):
            contract = self.relationships.get(edge.edge_name)
            if contract is None:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_RELATIONSHIP",
                        message=f"Unknown relationship: {edge.edge_name}",
                        location=f"edges.{edge_index}.edgeName",
                    )
                )
            else:
                source_type = node_types.get(edge.source_temp_id)
                target_type = node_types.get(edge.target_temp_id)
                if (
                    source_type != contract["sourceEntityType"]
                    or target_type != contract["targetEntityType"]
                ):
                    issues.append(
                        ValidationIssue(
                            code="RELATIONSHIP_DOMAIN_RANGE_MISMATCH",
                            message=f"{edge.edge_name} expects {contract['sourceEntityType']} -> {contract['targetEntityType']}",
                            location=f"edges.{edge_index}",
                        )
                    )
            issues.extend(
                self._validate_evidence(
                    edge.evidence, chunk_by_index, f"edges.{edge_index}.evidence"
                )
            )
        return issues

    @staticmethod
    def _validate_evidence(
        evidence_items, chunk_by_index, location: str
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for index, evidence in enumerate(evidence_items):
            chunk = chunk_by_index.get(evidence.chunk_index)
            if chunk is None:
                issues.append(
                    ValidationIssue(
                        code="EVIDENCE_UNKNOWN_CHUNK",
                        message=f"Evidence references unknown chunk {evidence.chunk_index}",
                        location=f"{location}.{index}",
                    )
                )
            elif _normalize_quote(evidence.text) not in _normalize_quote(chunk.text):
                issues.append(
                    ValidationIssue(
                        code="EVIDENCE_NOT_GROUNDED",
                        message="Evidence text must be a verbatim excerpt from its chunk",
                        location=f"{location}.{index}.text",
                        retryable=True,
                    )
                )
        return issues


class OntologyCache:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._projections: dict[str, OntologyProjection] = {}

    async def warm(self) -> OntologyProjection:
        core = await self._load("core")
        self._projections["core"] = core
        return core

    async def get(self, scope_key: str) -> OntologyProjection:
        normalized = scope_key.strip().casefold()
        if normalized not in ALLOWED_SCOPES:
            raise ValueError(f"Unsupported ontology scope: {scope_key}")
        if normalized not in self._projections:
            self._projections[normalized] = await self._load(normalized)
        return self._projections[normalized]

    def clear(self) -> None:
        self._projections.clear()

    async def _load(self, scope_key: str) -> OntologyProjection:
        async with self._session_factory() as session:
            versions = list(
                (
                    await session.scalars(
                        select(OntologyVersion).where(
                            OntologyVersion.status == OntologyVersionStatus.ACTIVE
                        )
                    )
                ).all()
            )
            if len(versions) != 1:
                raise RuntimeError(
                    f"Exactly one ACTIVE ontology version is required; found {len(versions)}"
                )
            version = versions[0]
            entities = list(
                (
                    await session.scalars(
                        select(OntologyEntityType).where(
                            OntologyEntityType.ontology_version_id == version.id
                        )
                    )
                ).all()
            )
            properties = list(
                (
                    await session.scalars(
                        select(OntologyProperty).where(
                            OntologyProperty.ontology_version_id == version.id
                        )
                    )
                ).all()
            )
            relationships = list(
                (
                    await session.scalars(
                        select(OntologyRelationship).where(
                            OntologyRelationship.ontology_version_id == version.id
                        )
                    )
                ).all()
            )
            aliases = list(
                (
                    await session.scalars(
                        select(OntologyAlias).where(
                            OntologyAlias.ontology_version_id == version.id
                        )
                    )
                ).all()
            )

        entity_by_id = {item.id: item for item in entities}
        selected_entities = [
            item for item in entities if _belongs_to_scope(item, scope_key)
        ]
        if scope_key != "core" and not selected_entities:
            selected_entities = entities
        selected_ids = {item.id for item in selected_entities}
        payload = {
            "versionId": str(version.id),
            "version": version.version,
            "scopeKey": scope_key,
            "entityTypes": [
                {
                    "technicalName": item.technical_name,
                    "displayName": item.display_name,
                    "description": item.description,
                    "identityStrategy": item.identity_strategy or {},
                }
                for item in selected_entities
            ],
            "properties": [
                {
                    "entityType": entity_by_id[item.entity_type_id].technical_name,
                    "technicalName": item.technical_name,
                    "displayName": item.display_name,
                    "dataType": item.data_type.value,
                    "required": item.required,
                    "multiValue": item.multi_value,
                    "constraints": item.constraints or {},
                }
                for item in properties
                if item.entity_type_id in selected_ids
            ],
            "relationships": [
                {
                    "technicalName": item.technical_name,
                    "displayName": item.display_name,
                    "sourceEntityType": entity_by_id[
                        item.source_entity_type_id
                    ].technical_name,
                    "targetEntityType": entity_by_id[
                        item.target_entity_type_id
                    ].technical_name,
                    "cardinality": item.cardinality.value,
                    "constraints": item.constraints or {},
                }
                for item in relationships
                if item.source_entity_type_id in selected_ids
                and item.target_entity_type_id in selected_ids
            ],
            "aliases": [
                {
                    "alias": item.alias,
                    "confidence": item.confidence,
                    "source": item.source.value,
                }
                for item in aliases
            ],
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        payload["digest"] = digest
        return OntologyProjection.model_validate(payload)


def _belongs_to_scope(entity: OntologyEntityType, scope_key: str) -> bool:
    if scope_key == "core":
        return True
    metadata = entity.metadata_ or {}
    scopes = metadata.get("scopes") or metadata.get("scope") or []
    if isinstance(scopes, str):
        scopes = [scopes]
    haystack = f"{entity.technical_name} {entity.display_name} {entity.description or ''}".casefold()
    return (
        scope_key in {str(value).casefold() for value in scopes}
        or scope_key in haystack
    )


def _matches_datatype(value: Any, data_type: str) -> bool:
    if data_type in {"STRING", "DATE", "DATETIME", "UUID"}:
        return isinstance(value, str)
    if data_type == "INTEGER":
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type == "FLOAT":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if data_type == "BOOLEAN":
        return isinstance(value, bool)
    if data_type == "JSON":
        return isinstance(value, (dict, list))
    return False


def _normalize_quote(value: str) -> str:
    return (
        unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    )


__all__ = ["ALLOWED_SCOPES", "COMPILER_VERSION", "OntologyCache", "OntologyRegistry"]
