"""Read compiled ontology snapshots and validate evidence-backed graph fragments.

The cache only stores active-version metadata, the lightweight scope catalog, and
compiled snapshots read from PostgreSQL.  Normalized ontology tables are consumed
by :class:`OntologyCompiler`, never assembled on the ingestion read path.
"""

import json
import logging
import re
import unicodedata
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    OntologyCompiledSnapshot,
    OntologyScope,
    OntologyVersion,
    OntologyVersionStatus,
)
from app.schemas.ingestion_schema import (
    ActiveOntology,
    GraphPatchFragment,
    OntologyProjection,
    OntologyScopeSummary,
    PreparedChunk,
    SemanticGraphPatchFragment,
    ValidationIssue,
)

COMPILER_VERSION = "ontology-compiler-v2"
logger = logging.getLogger(__name__)


def validate_coverage_integrity(
    fragment: GraphPatchFragment,
    chunks: Iterable[PreparedChunk],
    external_node_types: dict[str, str] | None = None,
) -> list[ValidationIssue]:
    """Validate that coverage decisions correspond to real graph contributions."""
    issues: list[ValidationIssue] = []
    chunk_by_index = {chunk.chunk_index: chunk for chunk in chunks}
    expected_coverage = set(chunk_by_index)
    coverage_positions: dict[int, list[int]] = {}
    for coverage_index, item in enumerate(fragment.coverage):
        coverage_positions.setdefault(item.chunk_index, []).append(coverage_index)

    supplied_coverage = set(coverage_positions)
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
    for chunk_index, positions in sorted(coverage_positions.items()):
        if len(positions) > 1:
            issues.append(
                ValidationIssue(
                    code="COVERAGE_DUPLICATE",
                    message=f"Chunk {chunk_index} has multiple coverage decisions",
                    location="coverage",
                    retryable=True,
                )
            )

    fact_chunks: set[int] = set()
    for node in fragment.nodes:
        for fact in node.properties:
            fact_chunks.update(item.chunk_index for item in fact.evidence)
    for edge in fragment.edges:
        fact_chunks.update(item.chunk_index for item in edge.evidence)

    for coverage_index, item in enumerate(fragment.coverage):
        if item.chunk_index not in expected_coverage:
            continue
        if item.decision == "MAPPED":
            if item.chunk_index not in fact_chunks:
                issues.append(
                    ValidationIssue(
                        code="MAPPED_WITHOUT_MAPPING",
                        message=(
                            f"Chunk {item.chunk_index} is marked MAPPED but no "
                            "property or edge carries evidence from that chunk"
                        ),
                        location=f"coverage.{coverage_index}.decision",
                        retryable=True,
                    )
                )
        elif item.decision == "DUPLICATE_EVIDENCE":
            # Must resolve to existing fact in current fragment or prior staged entities
            if not fact_chunks and not external_node_types:
                issues.append(
                    ValidationIssue(
                        code="DUPLICATE_WITHOUT_PRIOR_FACT",
                        message=(
                            f"Chunk {item.chunk_index} is marked DUPLICATE_EVIDENCE "
                            "but no prior or current knowledge facts exist to duplicate"
                        ),
                        location=f"coverage.{coverage_index}.decision",
                        retryable=True,
                    )
                )
        elif item.decision in {"SCHEMA_GAP", "UNSUPPORTED_BY_ONTOLOGY"}:
            issues.append(
                ValidationIssue(
                    code="SCHEMA_GAP_CANDIDATE",
                    message=(
                        f"Chunk {item.chunk_index} contains relevant knowledge "
                        f"that the loaded ontology cannot represent: {item.reason}"
                    ),
                    location=f"coverage.{coverage_index}.decision",
                    retryable=False,
                )
            )
        elif item.decision == "AMBIGUOUS":
            issues.append(
                ValidationIssue(
                    code="COVERAGE_AMBIGUOUS",
                    message=(
                        f"Chunk {item.chunk_index} is marked AMBIGUOUS: {item.reason}"
                    ),
                    location=f"coverage.{coverage_index}.decision",
                    retryable=True,
                )
            )
        elif item.decision == "FAILED":
            issues.append(
                ValidationIssue(
                    code="COVERAGE_FAILED",
                    message=(
                        f"Chunk {item.chunk_index} extraction failed: {item.reason}"
                    ),
                    location=f"coverage.{coverage_index}.decision",
                    retryable=True,
                )
            )
    return issues


class OntologyRegistry:
    """Bộ kiểm tra xác thực đối chiếu đồ thị tri thức với Schema Ontology.

    Nhiệm vụ:
        - Đối chiếu các Thực thể (`nodes`), Thuộc tính (`properties`) và Quan hệ (`edges`)
          trong `GraphPatchFragment` với phiên bản Ontology đang hoạt động (`ACTIVE`).
        - Kiểm tra tính xác thực của các trích dẫn bằng chứng (`evidence`) có trong đoạn văn bản nguồn hay không.
    """

    def __init__(self, projection: OntologyProjection) -> None:
        """Khởi tạo OntologyRegistry từ bản chiếu Ontology (OntologyProjection).

        Tham số:
            projection: Bản chiếu Schema Ontology chứa danh sách entity types, properties, relationships và aliases.
        """
        self.projection = projection
        self.entity_types = {
            item["technicalName"]: item for item in projection.entity_types
        }
        self.properties = {
            (item["entityType"], item["technicalName"]): item
            for item in projection.properties
        }
        self.relationships: dict[str, list[dict[str, Any]]] = {}
        for item in projection.relationships:
            self.relationships.setdefault(item["technicalName"], []).append(item)
        self.relationships_by_signature: dict[tuple[str, str, str], dict[str, Any]] = {
            (item["technicalName"], item["sourceEntityType"], item["targetEntityType"]): item
            for item in projection.relationships
        }
        self._entity_names = self._name_index(projection.entity_types)
        self._relationship_names = self._name_index(projection.relationships)
        self._property_names: dict[str, dict[str, str]] = {}
        for item in projection.properties:
            self._property_names.setdefault(item["entityType"], {})[
                item["technicalName"].casefold()
            ] = item["technicalName"]
        self._install_declared_aliases()

    @staticmethod
    def _name_index(items: Iterable[dict[str, Any]]) -> dict[str, str]:
        return {item["technicalName"].casefold(): item["technicalName"] for item in items}

    def _install_declared_aliases(self) -> None:
        targets: dict[tuple[str, str], tuple[str, str | None]] = {}
        for item in self.projection.entity_types:
            targets[("ENTITY_TYPE", str(item.get("id")))] = (
                item["technicalName"], None
            )
        for item in self.projection.relationships:
            targets[("RELATIONSHIP", str(item.get("id")))] = (
                item["technicalName"], None
            )
        for item in self.projection.properties:
            targets[("PROPERTY", str(item.get("id")))] = (
                item["technicalName"], item["entityType"]
            )
        for alias in self.projection.aliases:
            target = targets.get((alias.get("targetType"), str(alias.get("targetId"))))
            alias_value = alias.get("alias")
            if not target or not isinstance(alias_value, str):
                continue
            technical_name, entity_name = target
            if alias.get("targetType") == "ENTITY_TYPE":
                self._entity_names[alias_value.casefold()] = technical_name
            elif alias.get("targetType") == "RELATIONSHIP":
                self._relationship_names[alias_value.casefold()] = technical_name
            elif entity_name:
                self._property_names.setdefault(entity_name, {})[
                    alias_value.casefold()
                ] = technical_name

    def resolve_entity_name(self, value: str) -> str | None:
        return value if value in self.entity_types else self._entity_names.get(value.casefold())

    def resolve_property_name(self, class_name: str, value: str) -> str | None:
        if (class_name, value) in self.properties:
            return value
        return self._property_names.get(class_name, {}).get(value.casefold())

    def resolve_relationship_name(self, value: str) -> str | None:
        return value if value in self.relationships else self._relationship_names.get(value.casefold())

    def canonicalize_semantic_fragment(
        self, fragment: SemanticGraphPatchFragment
    ) -> SemanticGraphPatchFragment:
        """Apply exact, casefold and declared-alias resolution; never fuzzy match."""
        node_classes: dict[str, str] = {}
        nodes = []
        for node in fragment.nodes:
            class_name = self.resolve_entity_name(node.class_name) or node.class_name
            node_classes[node.temp_id] = class_name
            properties = [
                fact.model_copy(
                    update={
                        "property_name": self.resolve_property_name(
                            class_name, fact.property_name
                        )
                        or fact.property_name
                    }
                )
                for fact in node.properties
            ]
            nodes.append(
                node.model_copy(
                    update={"class_name": class_name, "properties": properties}
                )
            )
        edges = [
            edge.model_copy(
                update={
                    "edge_name": self.resolve_relationship_name(edge.edge_name)
                    or edge.edge_name
                }
            )
            for edge in fragment.edges
        ]
        return fragment.model_copy(update={"nodes": nodes, "edges": edges})

    def validate_fragment(
        self,
        fragment: GraphPatchFragment,
        chunks: Iterable[PreparedChunk],
        external_node_types: dict[str, str] | None = None,
    ) -> list[ValidationIssue]:
        """Xác thực toàn diện mảnh đồ thị (GraphPatchFragment) đối chiếu với các chunks văn bản.

        Kiểm tra:
            1. Phiên bản ontology (`ontology_version`) phải khớp với phiên bản đang nạp.
            2. Độ phủ (`coverage`): Mọi chunk trong batch phải có quyết định MAPPED hoặc NOT_RELEVANT.
            3. Tính hợp lệ của từng Nút (Node): Loại thực thể tồn tại, trường định danh hợp lệ.
            4. Thuộc tính (Property): Tên thuộc tính, kiểu dữ liệu, các ràng buộc và bằng chứng trích xuất.
            5. Mối quan hệ (Edge): Nút nguồn, nút đích, tên quan hệ hợp lệ và bằng chứng liên kết.

        Tham số:
            fragment: Mảnh đồ thị tri thức cần kiểm tra tính hợp lệ.
            chunks: Danh sách các đoạn văn bản nguồn (chunks) tương ứng trong batch.

        Trả về:
            Danh sách các lỗi / cảnh báo xác thực (`ValidationIssue`). Nếu danh sách rỗng nghĩa là hợp lệ.
        """
        issues: list[ValidationIssue] = []
        chunk_by_index = {chunk.chunk_index: chunk for chunk in chunks}

        # 1. Kiểm tra phiên bản Ontology
        if fragment.ontology_version not in {
            self.projection.version,
            self.projection.version_id,
        }:
            issues.append(
                ValidationIssue(
                    code="ONTOLOGY_VERSION_MISMATCH",
                    message="Fragment ontologyVersion does not match the job-pinned version",
                    location="ontologyVersion",
                )
            )

        # 2. Kiểm tra semantic coverage của từng chunk.
        issues.extend(
            validate_coverage_integrity(
                fragment,
                chunk_by_index.values(),
                external_node_types=external_node_types,
            )
        )

        # 3. Kiểm tra tính hợp lệ của từng Thực thể (Node) và các Thuộc tính (Properties)
        node_types: dict[str, str] = dict(external_node_types or {})
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
            else:
                entity_contract = self.entity_types[node.class_name]
                required_identity = (entity_contract.get("identityStrategy") or {}).get(
                    "required", []
                )
                supplied_properties = {fact.property_name for fact in node.properties}
                for field in required_identity:
                    if field not in node.identity:
                        issues.append(
                            ValidationIssue(
                                code="INTERNAL_IDENTITY_COMPILATION_ERROR",
                                message=f"Compiler omitted identity field {field} for {node.class_name}",
                                location=f"nodes.{node_index}.identity.{field}",
                            )
                        )
                    elif field not in supplied_properties:
                        issues.append(
                            ValidationIssue(
                                code="IDENTITY_SOURCE_PROPERTY_MISSING",
                                message=f"Identity source property {field} is missing for {node.class_name}",
                                location=f"nodes.{node_index}.properties",
                                retryable=True,
                            )
                        )
                    else:
                        source_value = next(
                            fact.value for fact in node.properties
                            if fact.property_name == field
                        )
                        normalized = source_value.strip() if isinstance(source_value, str) else source_value
                        if node.identity.get(field) != normalized:
                            issues.append(
                                ValidationIssue(
                                    code="IDENTITY_PROPERTY_MISMATCH",
                                    message=f"Identity field {field} does not match its source property",
                                    location=f"nodes.{node_index}.identity.{field}",
                                )
                            )
                for (entity_name, property_name), property_contract in self.properties.items():
                    if (
                        entity_name == node.class_name
                        and property_contract.get("required")
                        and property_name not in supplied_properties
                        and property_name not in node.identity
                    ):
                        issues.append(
                            ValidationIssue(
                                code="REQUIRED_PROPERTY_MISSING",
                                message=f"Property {property_name} is required for {node.class_name}",
                                location=f"nodes.{node_index}.properties",
                                retryable=True,
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
                elif not _matches_contract(fact.value, contract):
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

        # 4. Kiểm tra tính hợp lệ của các Mối quan hệ (Edges)
        for edge_index, edge in enumerate(fragment.edges):
            contracts = self.relationships.get(edge.edge_name)
            if not contracts:
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
                if source_type is None or target_type is None:
                    issues.append(
                        ValidationIssue(
                            code="UNKNOWN_ENTITY_REFERENCE",
                            message="Relationship endpoint does not resolve to a local or staged entity",
                            location=f"edges.{edge_index}",
                            retryable=True,
                        )
                    )
                    continue
                matching_contract = self.relationships_by_signature.get(
                    (edge.edge_name, source_type, target_type)
                )
                if matching_contract is None:
                    expected_pairs = [
                        f"{c['sourceEntityType']} -> {c['targetEntityType']}"
                        for c in contracts
                    ]
                    issues.append(
                        ValidationIssue(
                            code="RELATIONSHIP_DOMAIN_RANGE_MISMATCH",
                            message=f"{edge.edge_name} expects {' or '.join(expected_pairs)}",
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
        evidence_items, chunk_by_index: dict[int, PreparedChunk], location: str
    ) -> list[ValidationIssue]:
        """Kiểm tra trích dẫn chứng cứ (evidence) có đúng nguyên văn từ chunk nguồn không."""
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
            else:
                chunk_surfaces = [chunk.text]
                if chunk.section:
                    chunk_surfaces.extend([
                        f"{chunk.section}\n\n{chunk.text}",
                        f"{chunk.section}\n{chunk.text}",
                        chunk.section,
                    ])
                evidence_text = _normalize_quote(evidence.text)
                if any(evidence_text in _normalize_quote(surface) for surface in chunk_surfaces):
                    continue
                reason, preview = _evidence_failure_reason(evidence_text, _normalize_quote(chunk.text))
                logger.info(
                    "EVIDENCE_GROUNDING location=%s chunkIndex=%s reason=%s evidence=%r nearest=%r",
                    f"{location}.{index}.text",
                    evidence.chunk_index,
                    reason,
                    evidence_text[:120],
                    preview[:120],
                )
                issues.append(
                    ValidationIssue(
                        code="EVIDENCE_NOT_GROUNDED",
                        message=(
                            "Evidence text must be a verbatim excerpt from its chunk "
                            f"({reason}); evidence={evidence_text[:120]!r}; nearest={preview[:120]!r}"
                        ),
                        location=f"{location}.{index}.text",
                        retryable=True,
                    )
                )
        return issues


class OntologyCache:
    """Caches only catalog metadata and already-compiled immutable snapshots."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._active: ActiveOntology | None = None
        self._catalogs: dict[str, tuple[OntologyScopeSummary, ...]] = {}
        self._projections: dict[tuple[str, str], OntologyProjection] = {}

    async def warm(self) -> ActiveOntology:
        """Load the active version and lightweight scope catalog, never scope details."""
        async with self._session_factory() as session:
            versions = list(
                (await session.scalars(select(OntologyVersion).where(
                    OntologyVersion.status == OntologyVersionStatus.ACTIVE
                ))).all()
            )
            if len(versions) != 1:
                raise RuntimeError(
                    f"Exactly one ACTIVE ontology version is required; found {len(versions)}"
                )
            version = versions[0]
        self._active = ActiveOntology(version_id=str(version.id), version=version.version)
        await self.list_scopes(str(version.id), refresh=True)
        return self._active

    async def active_version(self) -> ActiveOntology:
        return self._active or await self.warm()

    async def list_scopes(
        self, ontology_version_id: str | None = None, *, refresh: bool = False
    ) -> tuple[OntologyScopeSummary, ...]:
        version_id = ontology_version_id or (await self.active_version()).version_id
        if not refresh and version_id in self._catalogs:
            return self._catalogs[version_id]
        async with self._session_factory() as session:
            rows = list((await session.execute(
                select(OntologyScope, OntologyCompiledSnapshot.schema_hash)
                .outerjoin(
                    OntologyCompiledSnapshot,
                    (OntologyCompiledSnapshot.ontology_version_id == OntologyScope.ontology_version_id)
                    & (OntologyCompiledSnapshot.scope_key == OntologyScope.scope_key),
                )
                .where(OntologyScope.ontology_version_id == uuid.UUID(version_id))
                .order_by(OntologyScope.scope_key)
            )).all())
        catalog = tuple(
            OntologyScopeSummary(
                id=str(scope.id),
                ontology_version_id=str(scope.ontology_version_id),
                scope_key=scope.scope_key,
                description=scope.description,
                summary=scope.summary or {},
                schema_hash=schema_hash,
            )
            for scope, schema_hash in rows
        )
        self._catalogs[version_id] = catalog
        return catalog

    async def get(
        self, scope_key: str, ontology_version_id: str | None = None
    ) -> OntologyProjection:
        version_id = ontology_version_id or (await self.active_version()).version_id
        normalized = scope_key.strip().casefold()
        catalog = {item.scope_key.casefold(): item for item in await self.list_scopes(version_id)}
        if normalized not in catalog:
            raise KeyError(f"Ontology scope does not exist in version {version_id}: {scope_key}")
        cache_key = (version_id, normalized)
        if cache_key not in self._projections:
            self._projections[cache_key] = await self._load_snapshot(version_id, normalized)
        return self._projections[cache_key]

    async def get_many(
        self, scope_keys: list[str], ontology_version_id: str
    ) -> OntologyProjection:
        normalized = list(dict.fromkeys(key.strip().casefold() for key in scope_keys if key.strip()))
        if not normalized:
            raise ValueError("At least one ontology scope must be selected")
        return merge_projections(
            [await self.get(key, ontology_version_id) for key in normalized]
        )

    async def refresh(self) -> ActiveOntology:
        self.clear()
        return await self.warm()

    def clear(self) -> None:
        self._active = None
        self._catalogs.clear()
        self._projections.clear()

    async def _load_snapshot(self, version_id: str, scope_key: str) -> OntologyProjection:
        async with self._session_factory() as session:
            snapshot = await session.scalar(
                select(OntologyCompiledSnapshot).where(
                    OntologyCompiledSnapshot.ontology_version_id == uuid.UUID(version_id),
                    func.lower(OntologyCompiledSnapshot.scope_key) == scope_key,
                )
            )
        if snapshot is None:
            raise RuntimeError(
                f"Compiled snapshot is missing for ontology {version_id}, scope {scope_key}"
            )
        if snapshot.expires_at and snapshot.expires_at <= datetime.now(UTC):
            raise RuntimeError(f"Compiled snapshot has expired: {scope_key}")
        payload = dict(snapshot.compiled_schema or {})
        payload.update(
            digest=snapshot.schema_hash,
            description=snapshot.description,
            compilerVersion=snapshot.compiler_version,
        )
        projection = OntologyProjection.model_validate(payload)
        if projection.version_id != version_id or projection.scope_key.casefold() != scope_key:
            raise RuntimeError(f"Compiled snapshot identity mismatch: {scope_key}")
        return projection


def merge_projections(projections: list[OntologyProjection]) -> OntologyProjection:
    """Merge compatible scope snapshots deterministically; conflicts are ontology errors."""
    if not projections:
        raise ValueError("No ontology projections were supplied")
    version_id = projections[0].version_id
    version = projections[0].version
    if any(item.version_id != version_id for item in projections):
        raise ValueError("Cannot merge ontology snapshots from different versions")

    def merged(items: Iterable[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
        result: dict[tuple[Any, ...], dict[str, Any]] = {}
        for item in items:
            key = tuple(item.get(field) for field in key_fields)
            current = result.get(key)
            if current is not None and _canonical(current) != _canonical(item):
                raise RuntimeError(f"Conflicting ontology definitions for {key}")
            result[key] = item
        return [result[key] for key in sorted(result, key=lambda value: tuple(str(x) for x in value))]

    entity_types = merged(
        (item for projection in projections for item in projection.entity_types),
        ("id", "technicalName"),
    )
    properties = merged(
        (item for projection in projections for item in projection.properties),
        ("id", "entityType", "technicalName"),
    )
    relationships = merged(
        (item for projection in projections for item in projection.relationships),
        ("id", "technicalName", "sourceEntityType", "targetEntityType"),
    )
    aliases = merged(
        (item for projection in projections for item in projection.aliases),
        ("id", "alias"),
    )
    entity_names = {item["technicalName"] for item in entity_types}
    bad_relationships = [item["technicalName"] for item in relationships if
                         item["sourceEntityType"] not in entity_names or item["targetEntityType"] not in entity_names]
    if bad_relationships:
        raise RuntimeError(f"Relationships reference entities outside merged scopes: {bad_relationships}")
    scope_keys = [item.scope_key for item in projections]
    payload = {
        "versionId": version_id, "version": version,
        "scopeKey": "+".join(scope_keys), "scopeKeys": scope_keys,
        "description": "\n".join(item.description for item in projections if item.description),
        "compilerVersion": COMPILER_VERSION,
        "entityTypes": entity_types, "properties": properties,
        "relationships": relationships, "aliases": aliases,
    }
    import hashlib
    payload["digest"] = hashlib.sha256(_canonical(payload).encode()).hexdigest()
    return OntologyProjection.model_validate(payload)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _matches_datatype(value: Any, data_type: str) -> bool:
    """Kiểm tra giá trị của thuộc tính có đúng kiểu dữ liệu khai báo trong Ontology không."""
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


def _matches_contract(value: Any, contract: dict[str, Any]) -> bool:
    if contract.get("multiValue"):
        if not isinstance(value, list):
            return False
        values = value
    else:
        if isinstance(value, list):
            return False
        values = [value]
    if not all(_matches_datatype(item, contract["dataType"]) for item in values):
        return False
    constraints = contract.get("constraints") or {}
    for item in values:
        if "enum" in constraints and item not in constraints["enum"]:
            return False
        if "min" in constraints and isinstance(item, (int, float)) and item < constraints["min"]:
            return False
        if "max" in constraints and isinstance(item, (int, float)) and item > constraints["max"]:
            return False
        if (
            "pattern" in constraints
            and isinstance(item, str)
            and re.fullmatch(constraints["pattern"], item) is None
        ):
            return False
    return True


def _normalize_quote(value: str) -> str:
    """Chuẩn hóa chuỗi trích dẫn (Unicode NFKC và ngắt dòng) để so khớp chính xác bằng chứng văn bản."""
    return (
        unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    )


def _evidence_failure_reason(evidence_text: str, chunk_text: str) -> tuple[str, str]:
    plain_evidence = re.sub(r"[*_`|]+", "", evidence_text)
    plain_chunk = re.sub(r"[*_`|]+", "", chunk_text)
    if plain_evidence and plain_evidence in plain_chunk:
        return "MARKDOWN_MISMATCH", _nearest_preview(plain_evidence, plain_chunk)
    if "\n" in chunk_text and evidence_text.replace("\n", " ") in chunk_text.replace("\n", " "):
        return "LINE_JOIN_MISMATCH", _nearest_preview(evidence_text.replace("\n", " "), chunk_text.replace("\n", " "))
    if evidence_text and any(line.startswith(evidence_text) or evidence_text.startswith(line) for line in chunk_text.splitlines() if line):
        return "TRUNCATED_QUOTE", _nearest_preview(evidence_text, chunk_text)
    return "TEXT_NOT_FOUND", _nearest_preview(evidence_text, chunk_text)


def _nearest_preview(needle: str, haystack: str) -> str:
    tokens = [token for token in re.split(r"\s+", needle.strip()) if token]
    for token in sorted(tokens, key=len, reverse=True):
        index = haystack.find(token)
        if index >= 0:
            return haystack[max(0, index - 60): index + len(token) + 60]
    return haystack[:160]


__all__ = ["COMPILER_VERSION", "OntologyCache", "OntologyRegistry", "merge_projections"]
