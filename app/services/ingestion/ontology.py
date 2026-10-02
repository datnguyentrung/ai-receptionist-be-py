"""Read compiled ontology snapshots and validate evidence-backed graph fragments.

The cache only stores active-version metadata, the lightweight scope catalog, and
compiled snapshots read from PostgreSQL.  Normalized ontology tables are consumed
by :class:`OntologyCompiler`, never assembled on the ingestion read path.
"""

import json
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
    ValidationIssue,
)

COMPILER_VERSION = "ontology-compiler-v2"


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

        # 2. Kiểm tra độ phủ (Coverage) của các chunks trong batch
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

        # 3. Kiểm tra tính hợp lệ của từng Thực thể (Node) và các Thuộc tính (Properties)
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
            else:
                entity_contract = self.entity_types[node.class_name]
                required_identity = (entity_contract.get("identityStrategy") or {}).get(
                    "required", []
                )
                supplied_properties = {fact.property_name for fact in node.properties}
                for field in required_identity:
                    if field not in node.identity and field not in supplied_properties:
                        issues.append(
                            ValidationIssue(
                                code="IDENTITY_FIELD_MISSING",
                                message=f"Identity field {field} is required for {node.class_name}",
                                location=f"nodes.{node_index}.identity.{field}",
                                retryable=True,
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


__all__ = ["COMPILER_VERSION", "OntologyCache", "OntologyRegistry", "merge_projections"]
