"""Các hàm nguyên thủy tất định (deterministic primitives) phục vụ công cụ nạp tài liệu (Ingestion ADK tools).

Luồng điều khiển ngữ nghĩa (semantic control flow) được định nghĩa trong SKILL.md.
Phân định vai trò lưu trữ:
- PostgreSQL: Lưu trữ bền vững ontology/schema, compiled snapshots và schema proposals.
- RAM (IngestionRepository): Quản lý trạng thái workspace, job, batches và chunks trong tiến trình hiện tại.
- Neo4j: Lưu trữ bền vững đồ thị tri thức (Knowledge Graph) sau khi nạp chính thức.

================================================================================
DANH SÁCH CÁC HÀM TRONG MODULE (GOM THEO NHÓM CHỨC NĂNG):

1. Nhóm Core Ingestion Lifecycle (Vòng đời nạp tài liệu & Knowledge Graph):
   - begin: Khởi tạo/tái sử dụng workspace ingestion, đọc và chia nhỏ tài liệu thành chunks & batches.
   - get_batch: Lấy thông tin các chunks và ngữ cảnh đồ thị chuẩn hóa (canonical context) của một batch.
   - submit_batch: Nhận kết quả trích xuất semantic, biên dịch, validate evidence/ontology và lưu staged fragment.
   - finalize: Kiểm tra tính toàn vẹn độ phủ (coverage integrity) trên toàn bộ batches và đánh dấu workspace sẵn sàng ghi.
   - fill: Thực hiện ghi chính thức dữ liệu đồ thị từ workspace vào Neo4j và cập nhật trạng thái COMMITTED.

2. Nhóm Ontology & Scope Operations (Quản lý & Tra cứu Ontology / Schema):
   - list_scopes: Liệt kê danh mục các ontology scopes có sẵn trong phiên bản ontology.
   - load_scope: Nạp thông tin chi tiết schema projection tương ứng với danh sách scope_keys.
   - validate_patch: Kiểm tra tính hợp lệ của graph patch độc lập dựa trên ontology projection.

3. Nhóm Quản lý tài liệu & Phiên bản (Document & Version Management):
   - delete_document: Đánh dấu xóa tài liệu và vô hiệu hóa version tương ứng trong Neo4j.
   - rollback_version: Đánh dấu rollback phiên bản tài liệu và vô hiệu hóa version trong Neo4j.

4. Nhóm Phân loại lỗi & Xử lý Ontology nội bộ (Ontology Error Classification):
   - _classify_missing_scopes: Phân loại lỗi ontology khi submit batch (phát hiện thiếu scope hay schema gap).
   - _missing_scope_issue: Định dạng lỗi khi thuộc tính/thực thể/quan hệ nằm ở scope khác chưa nạp.
   - _compatible_relationships: Lọc các quan hệ hợp lệ nối giữa hai loại thực thể nguồn và đích.

5. Nhóm Quản lý Workspace & Workflow Guard (Workspace & Workflow Validation):
   - required_workspace: Lấy workspace từ repository theo ingestion_id (ném lỗi nếu không tồn tại).
   - workflow_guard_payload: Kiểm tra hành động có hợp lệ với trạng thái hiện tại của job hay không.

6. Nhóm Định dạng phản hồi Payload (Payload Builders & Formatters):
   - batch_failure_payload: Tạo payload phản hồi khi một batch gặp lỗi hoặc vượt quá số lần thử lại.
   - status_payload: Tạo payload phản hồi trạng thái hiện tại của workspace ingestion.
   - error_payload: Tạo payload phản hồi lỗi tiêu chuẩn.

7. Nhóm Tiện ích nội bộ (Internal Helpers):
   - _document_key: Tạo key định danh tài liệu chuẩn hóa từ tên file.
   - _canonical_context: Lấy ngữ cảnh các thực thể đã được staged ở các batch trước đó.
   - _chunks_from_evidence: Tái tạo danh sách PreparedChunk từ evidence của GraphPatchFragment.
================================================================================
"""

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.enums import IngestionJobStatus, SourceVersionStatus
from app.schemas import (
    GraphNode,
    GraphPatchFragment,
    IngestionBatchData,
    PreparedChunk,
    SemanticGraphPatchFragment,
    SemanticGraphRepairDelta,
    ValidationIssue,
    Workspace,
)
from app.services.ingestion.engine.graph_store import Neo4jIngestionStore
from app.services.ingestion.engine.repository import IngestionRepository
from app.services.ingestion.engine.workflow_policy import evaluate_workflow
from app.services.ingestion.pipeline.reader import DocumentReader
from app.services.ingestion.schema.evidence_guard import GraphFragmentEvidenceGuard
from app.services.ingestion.schema.graph_patch_compiled import GraphPatchCompiler
from app.services.ingestion.schema.ontology import (
    COMPILER_VERSION,
    OntologyCache,
    OntologyRegistry,
    validate_coverage_integrity,
)
from app.services.ingestion.schema.repair_guard import RepairGuard
from app.utils import (
    stable_entity_key,
    staged_entity_index,
    workspace_chunks,
    workspace_fingerprint,
)

MAX_BATCH_VALIDATION_ATTEMPTS = max(
    1, int(os.getenv("INGESTION_MAX_BATCH_VALIDATION_ATTEMPTS", "2"))
)
logger = logging.getLogger(__name__)


# ============================================================================
# 1. NHÓM CORE INGESTION LIFECYCLE (VÒNG ĐỜI NẠP TÀI LIỆU & KNOWLEDGE GRAPH)
# ============================================================================


# ----------------------------------------------------------------------------
# Tên hàm: begin
# Chức năng:
#   - Kiểm tra kích thước tệp và đọc dữ liệu qua DocumentReader để tạo danh sách PreparedChunk.
#   - Tính mã băm nội dung (content_hash) và lấy phiên bản ontology đang hoạt động.
#   - Khởi tạo mới hoặc khôi phục workspace trong bộ nhớ RAM (IngestionRepository).
#   - Phân đoạn các chunk thành các batch theo giới hạn batch_size và max_batch_chars.
# Input:
#   - repository (IngestionRepository): Đối tượng quản lý trạng thái workspace trong RAM.
#   - ontology_cache (OntologyCache): Bộ nhớ đệm quản lý các schema ontology.
#   - artifact_name (str): Tên tệp hoặc artifact cần nạp.
#   - data (bytes): Dữ liệu nhị phân của tài liệu đầu vào.
#   - max_file_size (int): Kích thước tối đa cho phép của tệp (bytes).
#   - chunk_size_chars (int): Số ký tự ước tính cho mỗi chunk.
#   - batch_size (int): Số lượng chunk tối đa trong mỗi batch.
#   - skill_digest (str): Mã băm xác thực của skill/prompt nạp dữ liệu.
#   - model_id (str): Mã định danh mô hình AI thực hiện trích xuất.
#   - document_key (str | None): Khóa định danh tài liệu tùy chọn.
#   - scope_hint (str | None): Gợi ý phạm vi ontology ban đầu.
#   - mime_type (str | None): Định dạng MIME của tài liệu.
# Output:
#   - dict[str, Any]: Payload trạng thái workspace (status_payload), bao gồm ingestionId, stage,
#     thống kê chunks/batches và thông tin batch tiếp theo cần xử lý.
# ----------------------------------------------------------------------------
async def begin(
    repository: IngestionRepository,
    ontology_cache: OntologyCache,
    artifact_name: str,
    data: bytes,
    *,
    max_file_size: int,
    chunk_size_chars: int,
    batch_size: int,
    skill_digest: str,
    model_id: str,
    document_key: str | None = None,
    scope_hint: str | None = None,
    mime_type: str | None = None,
) -> Workspace:
    if len(data) > max_file_size:
        raise ValueError(f"Artifact exceeds the {max_file_size}-byte ingestion limit")
    reader = DocumentReader()
    chunks = reader.read_bytes(
        filename=artifact_name,
        data=data,
        mime_type=mime_type,
    )
    content_hash = hashlib.sha256(data).hexdigest()
    ontology = await ontology_cache.active_version()
    workspace, _resumed, _committed = await repository.create_or_resume(
        artifact_name=artifact_name,
        content_hash=content_hash,
        chunks=chunks,
        ontology=ontology,
        document_key=document_key or _document_key(artifact_name),
        scope_hint=scope_hint,
        skill_digest=skill_digest,
        model_id=model_id,
        compiler_version=COMPILER_VERSION,
        batch_size=batch_size,
        max_batch_chars=chunk_size_chars * batch_size,
    )
    logger.info(
        "BATCH_PARTITION ingestionId=%s batchSize=%s maxBatchChars=%s chunks=%s batches=%s",
        workspace.job.id,
        batch_size,
        chunk_size_chars * batch_size,
        len(workspace.chunks),
        [batch.chunk_indexes for batch in workspace.batches],
    )
    return workspace


async def repair_batch(
    repository: IngestionRepository,
    ontology_cache: OntologyCache,
    ingestion_id: str,
    batch_index: int,
    scope_keys: list[str],
    repair_delta: SemanticGraphRepairDelta,
) -> Workspace:
    """Apply a delta to the protected baseline for one repair-required batch."""

    workspace = await required_workspace(repository, ingestion_id)
    batch = _batch(workspace, batch_index)
    if batch.status != "REPAIR_REQUIRED":
        raise ValueError(f"Batch {batch_index} is not awaiting repair")
    if set(scope_keys) != set(batch.scope_keys):
        raise ValueError("Repair must reuse the selected scope set")
    if not batch.validated_baseline:
        raise ValueError("Batch has no protected baseline to repair")

    baseline = GraphPatchFragment.model_validate(batch.validated_baseline)
    if repair_delta.baseline_fingerprint != _baseline_fingerprint(baseline):
        return await repository.mark_batch_for_repair(
            ingestion_id,
            batch_index,
            [
                {
                    "code": "STALE_REPAIR_BASELINE",
                    "message": "Repair delta was generated from a stale protected baseline.",
                    "location": "baselineFingerprint",
                    "retryable": True,
                }
            ],
        )

    projection = await ontology_cache.get_many(
        scope_keys, str(workspace.job.ontology_version_id)
    )
    bindings = [
        {
            "scopeKey": key,
            "schemaHash": (
                await ontology_cache.get(key, str(workspace.job.ontology_version_id))
            ).digest,
        }
        for key in projection.scope_keys
    ]
    staged = staged_entity_index(workspace, before_batch=batch_index)
    baseline_refs = {
        node.temp_id: f"entity:{stable_entity_key(node.class_name, node.identity)}"
        for node in baseline.nodes
    }
    staged.update(
        {
            ref: {
                "ref": ref,
                "stableKey": ref.removeprefix("entity:"),
                "className": node.class_name,
                "identity": node.identity,
            }
            for node in baseline.nodes
            for ref in [baseline_refs[node.temp_id]]
        }
    )
    semantic = SemanticGraphPatchFragment.model_construct(
        nodes=repair_delta.nodes,
        edges=[
            edge.model_copy(
                update={
                    "source_temp_id": baseline_refs.get(
                        edge.source_temp_id, edge.source_temp_id
                    ),
                    "target_temp_id": baseline_refs.get(
                        edge.target_temp_id, edge.target_temp_id
                    ),
                }
            )
            for edge in repair_delta.edges
        ],
        coverage=repair_delta.coverage,
        warnings=[],
    )
    registry = OntologyRegistry(projection)
    compiled = GraphPatchCompiler().compile(
        registry.canonicalize_semantic_fragment(semantic), projection, staged_entities=staged
    )
    issues = list(compiled.issues)
    merged = _merge_repair_fragment(baseline, compiled.fragment, batch.graph_fragment, repair_delta.coverage)
    chunks = workspace_chunks(workspace, batch.chunk_indexes)
    if merged is not None:
        merged = GraphFragmentEvidenceGuard().canonicalize(merged, chunks)
        issues.extend(_repair_property_conflicts(merged, projection))
        issues.extend(_repair_edge_property_conflicts(baseline, merged))
        issues.extend(
            registry.validate_fragment(
                merged,
                chunks,
                external_node_types={item["stableKey"]: item["className"] for item in staged.values()},
            )
        )
        issues.extend(
            RepairGuard.compare(
                previous_canonical_fragment=baseline,
                new_canonical_fragment=merged,
                previous_validation_issues=batch.validation_issues,
            )
        )
    issue_dicts = [issue.model_dump(by_alias=True, mode="json") for issue in issues]
    if issue_dicts:
        issue_dicts = await _classify_missing_scopes(
            ontology_cache,
            str(workspace.job.ontology_version_id),
            projection,
            semantic,
            issue_dicts,
            external_node_types={ref: item["className"] for ref, item in staged.items()},
        )
    return await repository.store_batch_result(
        ingestion_id,
        batch_index,
        bindings,
        projection.digest,
        semantic,
        merged,
        issue_dicts,
        max_attempts=MAX_BATCH_VALIDATION_ATTEMPTS,
        validated_baseline=baseline if issue_dicts else None,
    )


def baseline_fingerprint(baseline: GraphPatchFragment) -> str:
    """Return a stable fingerprint for the repair contract's protected baseline."""

    payload = baseline.model_dump(by_alias=True, mode="json")
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


_baseline_fingerprint = baseline_fingerprint


def _repair_property_conflicts(
    fragment: GraphPatchFragment, projection: Any
) -> list[ValidationIssue]:
    """Reject distinct scalar values for one canonical node property."""

    issues: list[ValidationIssue] = []
    for node_index, node in enumerate(fragment.nodes):
        values: dict[str, set[str]] = {}
        for fact in node.properties:
            if isinstance(fact.value, list):
                continue
            values.setdefault(fact.property_name, set()).add(
                json.dumps(fact.value, ensure_ascii=False, sort_keys=True, default=str)
            )
        for property_name, distinct in values.items():
            if len(distinct) > 1:
                issues.append(
                    ValidationIssue(
                        code="REPAIR_SCALAR_PROPERTY_CONFLICT",
                        message=f"Repair produced multiple values for {node.class_name}.{property_name}",
                        location=f"nodes.{node_index}.properties",
                        retryable=True,
                    )
                )
    return issues


def _repair_edge_property_conflicts(
    baseline: GraphPatchFragment, candidate: GraphPatchFragment
) -> list[ValidationIssue]:
    """Reject a repair that overwrites a protected edge property."""

    protected = {
        (edge.edge_name, edge.source_temp_id, edge.target_temp_id): edge.properties
        for edge in baseline.edges
    }
    issues: list[ValidationIssue] = []
    for edge_index, edge in enumerate(candidate.edges):
        previous = protected.get((edge.edge_name, edge.source_temp_id, edge.target_temp_id))
        if previous is None:
            continue
        for key, value in previous.items():
            if key in edge.properties and edge.properties[key] != value:
                issues.append(
                    ValidationIssue(
                        code="REPAIR_EDGE_PROPERTY_CONFLICT",
                        message=f"Repair changed protected edge property {key}",
                        location=f"edges.{edge_index}.properties.{key}",
                        retryable=True,
                    )
                )
    return issues


def _merge_repair_fragment(
    baseline: GraphPatchFragment,
    delta: GraphPatchFragment | None,
    previous_fragment: dict | None,
    coverage_updates: list[Any],
) -> GraphPatchFragment | None:
    if delta is None:
        return None
    nodes = {stable_entity_key(node.class_name, node.identity): node for node in baseline.nodes}
    for node in delta.nodes:
        key = stable_entity_key(node.class_name, node.identity)
        previous = nodes.get(key)
        if previous is None:
            nodes[key] = node
            continue
        facts = {
            (fact.property_name, json.dumps(fact.value, ensure_ascii=False, sort_keys=True, default=str)): fact
            for fact in [*previous.properties, *node.properties]
        }
        nodes[key] = previous.model_copy(
            update={"properties": list(facts.values()), "evidence": [*previous.evidence, *node.evidence]}
        )
    edges = {
        (edge.edge_name, edge.source_temp_id, edge.target_temp_id): edge
        for edge in baseline.edges
    }
    for edge in delta.edges:
        key = (edge.edge_name, edge.source_temp_id, edge.target_temp_id)
        previous = edges.get(key)
        edges[key] = edge if previous is None else previous.model_copy(
            update={"properties": {**previous.properties, **edge.properties}, "evidence": [*previous.evidence, *edge.evidence]}
        )
    old_coverage = GraphPatchFragment.model_validate(previous_fragment).coverage if previous_fragment else []
    coverage = {item.chunk_index: item for item in old_coverage}
    coverage.update({item.chunk_index: item for item in coverage_updates})
    return GraphPatchFragment(
        ontology_version=delta.ontology_version,
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        coverage=list(coverage.values()),
        warnings=[*baseline.warnings, *delta.warnings],
    )


def _batch(workspace: Workspace, batch_index: int) -> IngestionBatchData:
    batch = next((item for item in workspace.batches if item.batch_index == batch_index), None)
    if batch is None:
        raise ValueError(f"Invalid batch index: {batch_index}")
    return batch


# ----------------------------------------------------------------------------
# Tên hàm: get_batch
# Chức năng:
#   - Kiểm tra điều kiện luồng công việc (workflow guard) cho hành động 'get_batch'.
#   - Truy xuất thông tin batch trong workspace theo batch_index.
#   - Lấy danh sách PreparedChunk tương ứng và ngữ cảnh thực thể chuẩn hóa (canonical context) từ các batch trước.
#   - Điều hướng hành động tiếp theo: 'load_scopes' (nếu đã có scopeKeys) hoặc 'list_scopes'.
# Input:
#   - repository (IngestionRepository): Đối tượng quản lý trạng thái workspace trong RAM.
#   - ingestion_id (str): Mã định danh phiên nạp tài liệu (job ID).
#   - batch_index (int): Chỉ số index của batch cần lấy dữ liệu (0-indexed).
# Output:
#   - dict[str, Any]: Payload chứa danh sách chunks, scopeHint, selectedScopeKeys, canonicalGraphContext,
#     stage ('batch_retrieved') và nextAction.
# ----------------------------------------------------------------------------
async def get_batch(
    repository: IngestionRepository,
    ingestion_id: str,
    batch_index: int,
) -> tuple[IngestionBatchData, tuple[PreparedChunk, ...], tuple[GraphNode, ...]]:
    workspace = await required_workspace(repository, ingestion_id)
    batch = next(
        (item for item in workspace.batches if item.batch_index == batch_index), None
    )
    if batch is None:
        raise ValueError(f"Invalid batch index: {batch_index}")
    chunks = workspace_chunks(workspace, batch.chunk_indexes)
    logger.info(
        "BATCH_START ingestionId=%s batchIndex=%s chunkIndexes=%s",
        ingestion_id,
        batch_index,
        batch.chunk_indexes,
    )

    nodes = tuple(
        GraphNode.model_validate(node)
        for previous in workspace.batches
        if previous.batch_index < batch_index
        and previous.status == "STAGED"
        and previous.graph_fragment
        for node in previous.graph_fragment.get("nodes", [])
    )
    return batch, tuple(chunks), nodes


# ----------------------------------------------------------------------------
# Tên hàm: submit_batch
# Chức năng:
#   - Xác thực và biên dịch mảnh đồ thị ngữ nghĩa (SemanticGraphPatchFragment) do AI trích xuất.
#   - Kiểm tra workflow guard và tính nhất quán của scope_keys khi chạy chế độ sửa chữa (repair).
#   - Biên dịch ngữ nghĩa thành canonical GraphPatchFragment và xác thực bằng chứng (evidence).
#   - Kiểm tra tính hợp lệ với ontology và so khớp với baseline đã xác thực trước đó (RepairGuard).
#   - Nếu có lỗi: phân loại lỗi ontology (_classify_missing_scopes), cập nhật baseline và lưu trạng thái REPAIR_REQUIRED/FAILED.
#   - Nếu hợp lệ: lưu kết quả với trạng thái STAGED vào workspace.
# Input:
#   - repository (IngestionRepository): Đối tượng quản lý trạng thái workspace trong RAM.
#   - ontology_cache (OntologyCache): Bộ nhớ đệm quản lý schema ontology.
#   - ingestion_id (str): Mã định danh phiên nạp tài liệu.
#   - batch_index (int): Chỉ số index của batch đang được nộp.
#   - scope_keys (list[str]): Danh sách khóa ontology scope áp dụng cho batch này.
#   - semantic_fragment (SemanticGraphPatchFragment): Dữ liệu trích xuất đồ thị ngữ nghĩa ban đầu.
# Output:
#   - dict[str, Any]: Payload kết quả xử lý (status_payload nếu thành công hoặc batch_failure_payload nếu có lỗi).
# ----------------------------------------------------------------------------
async def submit_batch(
    repository: IngestionRepository,
    ontology_cache: OntologyCache,
    ingestion_id: str,
    batch_index: int,
    scope_keys: list[str],
    semantic_fragment: dict[str, Any] | SemanticGraphPatchFragment,
) -> Workspace:
    workspace = await required_workspace(repository, ingestion_id)
    batch = next(
        (item for item in workspace.batches if item.batch_index == batch_index), None
    )
    if batch is None:
        raise ValueError(f"Invalid batch index: {batch_index}")

    if (
        batch.status == "REPAIR_REQUIRED"
        and batch.scope_keys
        and set(scope_keys) != set(batch.scope_keys)
    ):
        raise ValueError("Repair must reuse the previously selected scope set")

    projection = await ontology_cache.get_many(
        scope_keys, str(workspace.job.ontology_version_id)
    )
    bindings = [
        {
            "scopeKey": key,
            "schemaHash": (
                await ontology_cache.get(key, str(workspace.job.ontology_version_id))
            ).digest,
        }
        for key in projection.scope_keys
    ]
    try:
        semantic = (
            semantic_fragment
            if isinstance(semantic_fragment, SemanticGraphPatchFragment)
            else SemanticGraphPatchFragment.model_validate(semantic_fragment)
        )
    except ValidationError as exc:
        issues = [
            {
                "code": "INVALID_SEMANTIC_GRAPH_PATCH",
                "message": item["msg"],
                "location": ".".join(str(part) for part in item["loc"]),
                "retryable": True,
            }
            for item in exc.errors()
        ]
        workspace = await repository.store_batch_result(
            ingestion_id,
            batch_index,
            bindings,
            projection.digest,
            None,
            None,
            issues,
            max_attempts=MAX_BATCH_VALIDATION_ATTEMPTS,
        )
        return workspace

    registry = OntologyRegistry(projection)
    canonical_semantic = registry.canonicalize_semantic_fragment(semantic)
    entity_index = staged_entity_index(workspace, before_batch=batch_index)
    compile_result = GraphPatchCompiler().compile(
        canonical_semantic,
        projection,
        staged_entities=entity_index,
    )
    fragment = compile_result.fragment
    for edge_index, edge in enumerate(canonical_semantic.edges):
        logger.info(
            "EDGE_REFERENCE_RESOLUTION ingestionId=%s batchIndex=%s edgeIndex=%s source=%s target=%s status=%s",
            ingestion_id,
            batch_index,
            edge_index,
            edge.source_temp_id,
            edge.target_temp_id,
            "resolved"
            if fragment and edge_index < len(fragment.edges)
            else "unresolved",
        )
    resolved_by_temp_id = {
        item.temp_id: item.identity for item in (fragment.nodes if fragment else [])
    }
    required_by_class = {
        item["technicalName"]: (item.get("identityStrategy") or {}).get("required", [])
        for item in projection.entity_types
    }
    for node in canonical_semantic.nodes:
        logger.info(
            "IDENTITY_COMPILE tempId=%s className=%s requiredIdentityFields=%s resolvedIdentity=%s missingIdentityFields=%s",
            node.temp_id,
            node.class_name,
            required_by_class.get(node.class_name, []),
            json.dumps(resolved_by_temp_id.get(node.temp_id), ensure_ascii=False),
            [
                issue.message
                for issue in compile_result.issues
                if issue.location and issue.location.startswith("nodes.")
            ],
        )
    validation_issues = list(compile_result.issues)
    chunks = workspace_chunks(workspace, batch.chunk_indexes)
    if fragment is not None:
        fragment = GraphFragmentEvidenceGuard().canonicalize(fragment, chunks)
        validation_issues.extend(
            registry.validate_fragment(
                fragment,
                chunks,
                external_node_types={
                    item["stableKey"]: item["className"]
                    for item in entity_index.values()
                },
            )
        )

    if getattr(batch, "validated_baseline", None) and fragment is not None:
        try:
            previous_baseline = GraphPatchFragment.model_validate(
                batch.validated_baseline
            )
            validation_issues.extend(
                RepairGuard.compare(
                    previous_canonical_fragment=previous_baseline,
                    new_canonical_fragment=fragment,
                    previous_validation_issues=batch.validation_issues,
                )
            )
        except ValidationError:
            pass

    issues = [item.model_dump(by_alias=True, mode="json") for item in validation_issues]
    logger.info(
        "VALIDATION_RESULT ingestionId=%s batchIndex=%s issues=%s",
        ingestion_id,
        batch_index,
        json.dumps(issues, ensure_ascii=False),
    )
    if issues:
        issues = await _classify_missing_scopes(
            ontology_cache,
            str(workspace.job.ontology_version_id),
            projection,
            canonical_semantic,
            issues,
            external_node_types={
                ref: item["className"] for ref, item in entity_index.items()
            },
        )
        new_baseline = (
            RepairGuard.extract_validated_baseline(fragment, validation_issues)
            if fragment is not None
            else None
        )
        workspace = await repository.store_batch_result(
            ingestion_id,
            batch_index,
            bindings,
            projection.digest,
            semantic,
            fragment,
            issues,
            max_attempts=MAX_BATCH_VALIDATION_ATTEMPTS,
            validated_baseline=new_baseline,
        )
        return workspace

    workspace = await repository.store_batch_result(
        ingestion_id,
        batch_index,
        bindings,
        projection.digest,
        semantic,
        fragment,
        [],
        max_attempts=MAX_BATCH_VALIDATION_ATTEMPTS,
    )
    if fragment is not None:
        logger.info(
            "BATCH_STAGED ingestionId=%s batchIndex=%s canonicalNodes=%s edges=%s facts=%s",
            ingestion_id,
            batch_index,
            len(fragment.nodes),
            len(fragment.edges),
            sum(len(node.properties) for node in fragment.nodes) + len(fragment.edges),
        )
    return workspace


# ----------------------------------------------------------------------------
# Tên hàm: finalize
# Chức năng:
#   - Kiểm tra tính hoàn tất và độ phủ bằng chứng (coverage integrity) trên toàn bộ các batch.
#   - Đảm bảo tất cả batch đều ở trạng thái STAGED (không còn batch lỗi hoặc chờ duyệt schema).
#   - Nếu phát hiện thiếu sót bằng chứng, đánh dấu batch cần sửa chữa (REPAIR_REQUIRED).
#   - Nếu toàn bộ hợp lệ, cập nhật trạng thái workspace sang READY kèm readiness_fingerprint.
# Input:
#   - repository (IngestionRepository): Đối tượng quản lý trạng thái workspace trong RAM.
#   - ingestion_id (str): Mã định danh phiên nạp tài liệu.
# Output:
#   - dict[str, Any]: Payload trạng thái workspace (READY với nextAction='fill' nếu thành công,
#     hoặc payload yêu cầu sửa đổi/chờ duyệt nếu thất bại).
# ----------------------------------------------------------------------------
async def finalize(
    repository: IngestionRepository,
    ingestion_id: str,
) -> Workspace:
    workspace = await required_workspace(repository, ingestion_id)
    incomplete = [
        item.batch_index for item in workspace.batches if item.status != "STAGED"
    ]
    if incomplete:
        return workspace

    coverage_errors: dict[int, list[dict[str, Any]]] = {}
    for batch in workspace.batches:
        if not batch.graph_fragment:
            continue
        try:
            fragment = GraphPatchFragment.model_validate(batch.graph_fragment)
        except ValidationError as exc:
            coverage_errors[batch.batch_index] = [
                {
                    "code": "INVALID_STAGED_GRAPH_FRAGMENT",
                    "message": item["msg"],
                    "location": ".".join(str(part) for part in item["loc"]),
                    "retryable": True,
                }
                for item in exc.errors()
            ]
            continue

        issues = validate_coverage_integrity(
            fragment,
            workspace_chunks(workspace, batch.chunk_indexes),
        )
        if issues:
            coverage_errors[batch.batch_index] = [
                issue.model_dump(by_alias=True, mode="json") for issue in issues
            ]

    if coverage_errors:
        for batch_index, issues in coverage_errors.items():
            workspace = await repository.mark_batch_for_repair(
                ingestion_id, batch_index, issues
            )
        return workspace

    return await repository.mark_ready(ingestion_id, workspace_fingerprint(workspace))


# ----------------------------------------------------------------------------
# Tên hàm: fill
# Chức năng:
#   - Ghi chính thức toàn bộ tri thức đồ thị từ workspace vào cơ sở dữ liệu đồ thị Neo4j.
#   - Kiểm tra điều kiện tiên quyết: workspace phải ở trạng thái READY và khớp readiness_fingerprint.
#   - Chuyển trạng thái sang WRITING, thực hiện ghi dữ liệu vào Neo4j, vô hiệu hóa phiên bản cũ (nếu có).
#   - Cập nhật trạng thái COMMITTED trong repository hoặc xử lý đối soát (reconcile) nếu xảy ra sự cố.
# Input:
#   - repository (IngestionRepository): Đối tượng quản lý trạng thái workspace trong RAM.
#   - graph_store (Neo4jIngestionStore): Đối tượng kết nối và thao tác với Neo4j.
#   - ingestion_id (str): Mã định danh phiên nạp tài liệu.
# Output:
#   - dict[str, Any]: Payload trạng thái COMMITTED kèm số lượng entities/relationships đã lưu thành công vào Neo4j.
# ----------------------------------------------------------------------------
async def fill(
    repository: IngestionRepository,
    graph_store: Neo4jIngestionStore,
    ingestion_id: str,
) -> dict[str, Any]:
    workspace = await required_workspace(repository, ingestion_id)
    guarded = workflow_guard_payload(workspace, "fill")
    if guarded is not None:
        return guarded
    if workspace.job.status == IngestionJobStatus.COMMITTED:
        return status_payload(workspace, idempotent=True)
    expected = workspace_fingerprint(workspace)
    if (
        workspace.job.status != IngestionJobStatus.READY
        or workspace.job.readiness_fingerprint != expected
    ):
        return error_payload(
            "validation_precondition",
            ingestion_id,
            "VALIDATION_PRECONDITION",
            "finalize_ingestion must succeed before fill_ingestion",
        )
    await repository.mark_writing(ingestion_id)
    try:
        result = await graph_store.fill(workspace)
        previous_version_id = workspace.document.current_version_id
        if previous_version_id and previous_version_id != workspace.version.id:
            result["superseded"] = await graph_store.deactivate_version(
                str(previous_version_id), "SUPERSEDED"
            )
        committed = await repository.mark_committed(ingestion_id, result)
        return {**status_payload(committed), **result}
    except Exception as exc:  # noqa: BLE001 - reconcile cross-store commit
        reconciled = await graph_store.committed_summary(str(workspace.version.id))
        if reconciled is not None:
            committed = await repository.mark_committed(ingestion_id, reconciled)
            return {**status_payload(committed), **reconciled}
        await repository.mark_failed(ingestion_id, "persistence_failure", str(exc))
        return error_payload(
            "persistence_failure", ingestion_id, "PERSISTENCE_FAILED", str(exc)
        )


# ============================================================================
# 2. NHÓM ONTOLOGY & SCOPE OPERATIONS (QUẢN LÝ & TRA CỨU ONTOLOGY / SCHEMA)
# ============================================================================


# ----------------------------------------------------------------------------
# Tên hàm: list_scopes
# Chức năng:
#   - Truy vấn danh mục các ontology scope có sẵn trong một phiên bản ontology cụ thể.
# Input:
#   - ontology_cache (OntologyCache): Bộ nhớ đệm quản lý schema ontology.
#   - ontology_version_id (str | None): Mã định danh phiên bản ontology (nếu không truyền sẽ dùng active version).
# Output:
#   - dict[str, Any]: Payload chứa danh sách các scope (mô tả, entity types, relationship types) và mã phiên bản.
# ----------------------------------------------------------------------------
async def list_scopes(
    ontology_cache: OntologyCache, ontology_version_id: str | None = None
) -> dict[str, Any]:
    ontology = await ontology_cache.active_version()
    version_id = ontology_version_id or ontology.version_id
    scopes = await ontology_cache.list_scopes(version_id)
    return {
        "success": True,
        "stage": "scope_catalog_loaded",
        "ontologyVersionId": version_id,
        "ontologyVersion": ontology.version
        if version_id == ontology.version_id
        else None,
        "scopes": [item.model_dump(by_alias=True, mode="json") for item in scopes],
    }


# ----------------------------------------------------------------------------
# Tên hàm: load_scope
# Chức năng:
#   - Nạp chi tiết projection schema (thực thể, thuộc tính, quan hệ) cho tập hợp các scope_keys được chọn.
# Input:
#   - ontology_cache (OntologyCache): Bộ nhớ đệm quản lý schema ontology.
#   - scope_keys (list[str]): Danh sách các khóa scope cần nạp.
#   - ontology_version_id (str): Mã định danh phiên bản ontology.
# Output:
#   - dict[str, Any]: Payload chứa chi tiết schema projection và gợi ý nextAction ('submit_batch').
# ----------------------------------------------------------------------------
async def load_scope(
    ontology_cache: OntologyCache,
    scope_keys: list[str],
    ontology_version_id: str,
) -> dict[str, Any]:
    projection = await ontology_cache.get_many(scope_keys, ontology_version_id)
    return {
        "success": True,
        "stage": "schema_loaded",
        "terminal": False,
        "ingestionId": None,
        "nextAction": "submit_batch",
        "scope": projection.model_dump(by_alias=True, mode="json"),
    }


# ----------------------------------------------------------------------------
# Tên hàm: validate_patch
# Chức năng:
#   - Xác thực tính hợp lệ độc lập của một graph patch fragment dạng dictionary với ontology projection.
# Input:
#   - ontology_cache (OntologyCache): Bộ nhớ đệm quản lý schema ontology.
#   - graph_patch (dict[str, Any]): Dữ liệu đồ thị cần kiểm tra cấu trúc và tính hợp lệ.
#   - scope_keys (list[str]): Danh sách các khóa scope áp dụng kiểm tra.
#   - ontology_version_id (str): Mã định danh phiên bản ontology.
# Output:
#   - dict[str, Any]: Kết quả xác thực bao gồm cờ valid (True/False) và danh sách lỗi (nếu có).
# ----------------------------------------------------------------------------
async def validate_patch(
    ontology_cache: OntologyCache,
    graph_patch: dict[str, Any],
    scope_keys: list[str],
    ontology_version_id: str,
) -> dict[str, Any]:
    try:
        fragment = GraphPatchFragment.model_validate(graph_patch)
    except ValidationError as exc:
        return {
            "success": False,
            "stage": "graph_validation",
            "terminal": True,
            "ingestionId": None,
            "nextAction": None,
            "errors": exc.errors(include_url=False),
        }
    projection = await ontology_cache.get_many(scope_keys, ontology_version_id)
    issues = OntologyRegistry(projection).validate_fragment(
        fragment, _chunks_from_evidence(fragment)
    )
    return {
        "success": not issues,
        "stage": "graph_validation",
        "terminal": True,
        "ingestionId": None,
        "nextAction": None,
        "valid": not issues,
        "scopeKeys": projection.scope_keys,
        "errors": [item.model_dump(by_alias=True, mode="json") for item in issues],
    }


# ============================================================================
# 3. NHÓM QUẢN LÝ TÀI LIỆU & PHIÊN BẢN (DOCUMENT & VERSION MANAGEMENT)
# ============================================================================


# ----------------------------------------------------------------------------
# Tên hàm: delete_document
# Chức năng:
#   - Đánh dấu trạng thái phiên bản tài liệu thành DELETED trong repository.
#   - Vô hiệu hóa (deactivate) các nút và cạnh tương ứng của phiên bản tài liệu trong Neo4j.
# Input:
#   - repository (IngestionRepository): Đối tượng quản lý trạng thái workspace trong RAM.
#   - graph_store (Neo4jIngestionStore): Đối tượng kết nối và thao tác với Neo4j.
#   - document_id (str): Mã định danh tài liệu cần xóa.
#   - if_missing (str): Cách xử lý khi không tìm thấy tài liệu ('ignore' hoặc ném lỗi).
# Output:
#   - dict[str, Any]: Payload xác nhận xóa tài liệu thành công.
# ----------------------------------------------------------------------------
async def delete_document(
    repository: IngestionRepository,
    graph_store: Neo4jIngestionStore,
    document_id: str,
    if_missing: str,
) -> dict[str, Any]:
    try:
        document, version = await repository.set_document_version_status(
            document_id, None, SourceVersionStatus.DELETED
        )
    except KeyError:
        if if_missing == "ignore":
            return {
                "success": True,
                "stage": "deleted",
                "terminal": True,
                "ingestionId": None,
                "nextAction": None,
                "documentId": document_id,
                "missing": True,
            }
        return error_payload("delete", None, "DOCUMENT_NOT_FOUND", document_id)
    result = await graph_store.deactivate_version(str(version.id), "DELETED")
    return {
        "success": True,
        "stage": "deleted",
        "terminal": True,
        "ingestionId": None,
        "nextAction": None,
        "documentId": str(document.id),
        **result,
    }


# ----------------------------------------------------------------------------
# Tên hàm: rollback_version
# Chức năng:
#   - Đánh dấu trạng thái một phiên bản tài liệu cụ thể thành ROLLED_BACK trong repository.
#   - Vô hiệu hóa phiên bản đó trong cơ sở dữ liệu đồ thị Neo4j.
# Input:
#   - repository (IngestionRepository): Đối tượng quản lý trạng thái workspace trong RAM.
#   - graph_store (Neo4jIngestionStore): Đối tượng kết nối và thao tác với Neo4j.
#   - document_id (str): Mã định danh tài liệu.
#   - version_id (str): Mã định danh phiên bản cần rollback.
# Output:
#   - dict[str, Any]: Payload xác nhận rollback phiên bản thành công.
# ----------------------------------------------------------------------------
async def rollback_version(
    repository: IngestionRepository,
    graph_store: Neo4jIngestionStore,
    document_id: str,
    version_id: str,
) -> dict[str, Any]:
    try:
        document, version = await repository.set_document_version_status(
            document_id, version_id, SourceVersionStatus.ROLLED_BACK
        )
    except KeyError:
        return error_payload("rollback", None, "VERSION_NOT_FOUND", version_id)
    result = await graph_store.deactivate_version(str(version.id), "ROLLED_BACK")
    return {
        "success": True,
        "stage": "rolled_back",
        "terminal": True,
        "ingestionId": None,
        "nextAction": None,
        "documentId": str(document.id),
        **result,
    }


# ============================================================================
# 4. NHÓM PHÂN LOẠI LỖI & XỬ LÝ ONTOLOGY NỘI BỘ (ONTOLOGY ERROR CLASSIFICATION)
# ============================================================================


# ----------------------------------------------------------------------------
# Tên hàm: _classify_missing_scopes
# Chức năng:
#   - Phân loại nguyên nhân lỗi ontology khi submit batch.
#   - Phân biệt giữa: thiếu scope chưa nạp (MISSING_SCOPE), sai ánh xạ quan hệ trong scope (RELATIONSHIP_MAPPING_MISMATCH)
#     và khoảng trống ontology thực sự (SCHEMA_GAP_CANDIDATE).
# Input:
#   - ontology_cache (OntologyCache): Bộ nhớ đệm schema ontology.
#   - ontology_version_id (str): Mã định danh phiên bản ontology được ghim.
#   - selected_projection: Schema projection hiện tại của batch.
#   - semantic (SemanticGraphPatchFragment): Mảnh đồ thị ngữ nghĩa gốc do AI sinh ra.
#   - issues (list[dict[str, Any]]): Danh sách lỗi kiểm tra hợp lệ ban đầu.
#   - external_node_types (dict[str, str] | None): Bản đồ ánh xạ ID thực thể ngoài (đã staged) sang tên lớp.
# Output:
#   - list[dict[str, Any]]: Danh sách các lỗi đã được phân loại chi tiết và bổ sung thông tin định hướng sửa đổi.
# ----------------------------------------------------------------------------
async def _classify_missing_scopes(
    ontology_cache: OntologyCache,
    ontology_version_id: str,
    selected_projection: Any,
    semantic: SemanticGraphPatchFragment,
    issues: list[dict[str, Any]],
    *,
    external_node_types: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    relevant_codes = {
        "UNKNOWN_ENTITY_TYPE",
        "UNKNOWN_PROPERTY",
        "UNKNOWN_RELATIONSHIP",
        "RELATIONSHIP_DOMAIN_RANGE_MISMATCH",
    }
    if not any(item.get("code") in relevant_codes for item in issues):
        return issues

    selected_scope_keys = list(getattr(selected_projection, "scope_keys", []) or [])
    node_types = {node.temp_id: node.class_name for node in semantic.nodes}
    node_types.update(external_node_types or {})

    other_keys: list[str] = []
    other_registry = None
    if hasattr(ontology_cache, "list_scopes"):
        catalog = await ontology_cache.list_scopes(ontology_version_id)
        other_keys = [
            item.scope_key
            for item in catalog
            if item.scope_key not in selected_scope_keys
        ]
        if other_keys:
            other_projection = await ontology_cache.get_many(
                other_keys, ontology_version_id
            )
            other_registry = OntologyRegistry(other_projection)

    classified: list[dict[str, Any]] = []
    for issue in issues:
        code = issue.get("code")
        location = str(issue.get("location") or "")
        parts = location.split(".")
        updated = issue
        try:
            if code == "UNKNOWN_ENTITY_TYPE" and other_registry is not None:
                node = semantic.nodes[int(parts[1])]
                if other_registry.resolve_entity_name(node.class_name) is not None:
                    updated = _missing_scope_issue(issue)

            elif code == "UNKNOWN_PROPERTY" and other_registry is not None:
                node = semantic.nodes[int(parts[1])]
                fact = node.properties[int(parts[3])]
                other_class = other_registry.resolve_entity_name(node.class_name)
                if other_class and other_registry.resolve_property_name(
                    other_class, fact.property_name
                ):
                    updated = _missing_scope_issue(issue)

            elif code == "UNKNOWN_RELATIONSHIP" and other_registry is not None:
                edge = semantic.edges[int(parts[1])]
                if other_registry.resolve_relationship_name(edge.edge_name) is not None:
                    updated = _missing_scope_issue(issue)

            elif code == "RELATIONSHIP_DOMAIN_RANGE_MISMATCH":
                edge = semantic.edges[int(parts[1])]
                source_type = node_types.get(edge.source_temp_id)
                target_type = node_types.get(edge.target_temp_id)
                if source_type and target_type:
                    selected_candidates = _compatible_relationships(
                        selected_projection.relationships,
                        source_type,
                        target_type,
                    )
                    if selected_candidates:
                        updated = {
                            **issue,
                            "code": "RELATIONSHIP_MAPPING_MISMATCH",
                            "message": (
                                f"{issue['message']}; compatible relationship(s) "
                                f"in the loaded schema: {selected_candidates}"
                            ),
                            "retryable": True,
                            "candidateRelationships": selected_candidates,
                        }
                    else:
                        scope_candidates: dict[str, list[str]] = {}
                        if hasattr(ontology_cache, "get"):
                            for scope_key in other_keys:
                                projection = await ontology_cache.get(
                                    scope_key, ontology_version_id
                                )
                                candidates = _compatible_relationships(
                                    projection.relationships,
                                    source_type,
                                    target_type,
                                )
                                if candidates:
                                    scope_candidates[scope_key] = candidates
                        if scope_candidates:
                            updated = {
                                **_missing_scope_issue(issue),
                                "candidateScopes": sorted(scope_candidates),
                                "candidateRelationships": scope_candidates,
                            }
                        else:
                            updated = {
                                **issue,
                                "code": "SCHEMA_GAP_CANDIDATE",
                                "message": (
                                    f"{issue['message']}; no relationship in the "
                                    f"pinned ontology supports {source_type} -> {target_type}"
                                ),
                                "retryable": False,
                                "sourceEntityType": source_type,
                                "targetEntityType": target_type,
                            }
        except (IndexError, KeyError, TypeError, ValueError):
            updated = issue
        classified.append(updated)
    return classified


# ----------------------------------------------------------------------------
# Tên hàm: _missing_scope_issue
# Chức năng:
#   - Chuyển đổi mã lỗi thành 'MISSING_SCOPE' và thêm chú thích rằng khái niệm tồn tại ở scope khác.
# Input:
#   - issue (dict[str, Any]): Dictionary chứa thông tin lỗi ban đầu.
# Output:
#   - dict[str, Any]: Dictionary lỗi đã được cập nhật mã MISSING_SCOPE và cờ retryable=True.
# ----------------------------------------------------------------------------
def _missing_scope_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return {
        **issue,
        "code": "MISSING_SCOPE",
        "message": (
            f"{issue['message']}; the concept exists in another scope "
            "of the pinned ontology version"
        ),
        "retryable": True,
    }


# ----------------------------------------------------------------------------
# Tên hàm: _compatible_relationships
# Chức năng:
#   - Tìm danh sách các tên quan hệ hợp lệ nối giữa hai loại thực thể nguồn và đích trong danh sách schema quan hệ.
# Input:
#   - relationships (list[dict[str, Any]]): Danh sách định nghĩa quan hệ trong schema.
#   - source_type (str): Tên kỹ thuật của loại thực thể nguồn.
#   - target_type (str): Tên kỹ thuật của loại thực thể đích.
# Output:
#   - list[str]: Danh sách tên kỹ thuật (technicalName) của các quan hệ tương thích (đã sắp xếp).
# ----------------------------------------------------------------------------
def _compatible_relationships(
    relationships: list[dict[str, Any]],
    source_type: str,
    target_type: str,
) -> list[str]:
    return sorted(
        {
            item["technicalName"]
            for item in relationships
            if item.get("sourceEntityType") == source_type
            and item.get("targetEntityType") == target_type
            and item.get("technicalName")
        }
    )


# ============================================================================
# 5. NHÓM QUẢN LÝ WORKSPACE & WORKFLOW GUARD (WORKSPACE & WORKFLOW VALIDATION)
# ============================================================================


# ----------------------------------------------------------------------------
# Tên hàm: required_workspace
# Chức năng:
#   - Lấy đối tượng Workspace từ IngestionRepository theo ingestion_id. Ném ngoại lệ KeyError nếu không tìm thấy.
# Input:
#   - repository (IngestionRepository): Đối tượng quản lý trạng thái workspace trong RAM.
#   - ingestion_id (str): Mã định danh phiên nạp tài liệu.
# Output:
#   - Workspace: Đối tượng Workspace chứa toàn bộ trạng thái phiên làm việc.
# ----------------------------------------------------------------------------
async def required_workspace(
    repository: IngestionRepository, ingestion_id: str
) -> Workspace:
    workspace = await repository.get_workspace(ingestion_id)
    if workspace is None:
        raise KeyError(f"Unknown ingestionId: {ingestion_id}")
    return workspace


# ----------------------------------------------------------------------------
# Tên hàm: workflow_guard_payload
# Chức năng:
#   - Kiểm tra xem hành động yêu cầu có hợp lệ với trạng thái hiện tại của job hay không (evaluate_workflow).
#   - Trả về None nếu hợp lệ, hoặc trả về payload chứa thông báo bị chặn (blockedAction) nếu vi phạm.
# Input:
#   - workspace (Workspace): Đối tượng Workspace hiện tại.
#   - action (str): Tên hành động cần kiểm tra (ví dụ: 'get_batch', 'submit_batch', 'finalize', 'fill').
# Output:
#   - dict[str, Any] | None: None nếu được phép thực hiện, hoặc dictionary payload mô tả hành động bị chặn.
# ----------------------------------------------------------------------------
def workflow_guard_payload(
    workspace: Workspace,
    action: str,
) -> dict[str, Any] | None:
    decision = evaluate_workflow(workspace.job.status, action)
    if decision.allowed:
        return None
    result = status_payload(workspace)
    result["blockedAction"] = action
    if decision.next_action is not None:
        result["nextAction"] = decision.next_action
    return result


# ============================================================================
# 6. NHÓM ĐỊNH DẠNG PHẢN HỒI PAYLOAD (PAYLOAD BUILDERS & FORMATTERS)
# ============================================================================


# ----------------------------------------------------------------------------
# Tên hàm: batch_failure_payload
# Chức năng:
#   - Tạo cấu trúc dữ liệu phản hồi khi một batch gặp lỗi xác thực hoặc vượt quá số lần thử lại cho phép.
#   - Trả về trạng thái FAILED (explicit_extraction_failure) nếu quá số lần thử, ngược lại trả về REPAIR_REQUIRED.
# Input:
#   - workspace (Workspace): Đối tượng Workspace hiện tại.
#   - batch_index (int): Chỉ số index của batch bị lỗi.
#   - issues (list[dict]): Danh sách các lỗi xác thực gặp phải.
#   - max_attempts (int): Số lần thử lại tối đa cho phép.
# Output:
#   - dict[str, Any]: Payload mô tả chi tiết lỗi và định hướng hành động tiếp theo.
# ----------------------------------------------------------------------------
def batch_failure_payload(
    workspace: Workspace,
    batch_index: int,
    issues: list[dict],
    max_attempts: int = MAX_BATCH_VALIDATION_ATTEMPTS,
) -> dict[str, Any]:
    batch = next(item for item in workspace.batches if item.batch_index == batch_index)
    if batch.status == "FAILED":
        return {
            "success": False,
            "stage": "explicit_extraction_failure",
            "terminal": True,
            "retryRequired": False,
            "nextAction": "explicit_extraction_failure",
            "ingestionId": str(workspace.job.id),
            "batchIndex": batch_index,
            "affectedChunkIndexes": list(getattr(batch, "chunk_indexes", [])),
            "attempt": batch.validation_attempts,
            "maxAttempts": max_attempts,
            "errors": [
                {
                    "code": "BATCH_VALIDATION_RETRY_LIMIT_EXCEEDED",
                    "message": f"Batch {batch_index} exceeded the validation retry limit",
                },
                *issues,
            ],
        }
    return {
        "success": False,
        "stage": "repair_required",
        "terminal": False,
        "retryRequired": True,
        "nextAction": "repair_batch",
        "ingestionId": str(workspace.job.id),
        "batchIndex": batch_index,
        "scopeKeys": getattr(batch, "scope_keys", []),
        "affectedChunkIndexes": list(getattr(batch, "chunk_indexes", [])),
        "errors": issues,
        "validationAttempts": getattr(batch, "validation_attempts", 0),
        "maxAttempts": max_attempts,
    }


# ----------------------------------------------------------------------------
# Tên hàm: status_payload
# Chức năng:
#   - Tổng hợp và tạo payload phản hồi trạng thái toàn diện của workspace nạp tài liệu.
#   - Xác định giai đoạn (stage) hiện tại, các số liệu thống kê (chunks, batches, staged) và batch tiếp theo cần xử lý.
# Input:
#   - workspace (Workspace): Đối tượng Workspace hiện tại.
#   - resumed (bool): Cờ đánh dấu tiến trình được khôi phục.
#   - idempotent (bool): Cờ đánh dấu thao tác idempotent (đã commit từ trước).
# Output:
#   - dict[str, Any]: Payload trạng thái hoàn chỉnh của workspace.
# ----------------------------------------------------------------------------
def status_payload(
    workspace: Workspace,
    *,
    resumed: bool = False,
    idempotent: bool = False,
) -> dict[str, Any]:
    pending = next(
        (item for item in workspace.batches if item.status != "STAGED"), None
    )
    if workspace.job.status == IngestionJobStatus.COMMITTED:
        stage, terminal, next_action = "committed", True, None
    elif workspace.job.status == IngestionJobStatus.FAILED:
        stage, terminal, next_action = (
            "explicit_extraction_failure",
            True,
            "explicit_extraction_failure",
        )
    elif workspace.job.status == IngestionJobStatus.READY:
        stage, terminal, next_action = "ready_to_fill", False, "fill"
    elif pending is None:
        stage, terminal, next_action = "ready_to_finalize", False, "finalize"
    elif pending.status == "BLOCKED_SCHEMA":
        stage, terminal, next_action = (
            "awaiting_schema_approval",
            False,
            "wait_for_schema_review",
        )
    elif pending.status == "REPAIR_REQUIRED":
        stage, terminal, next_action = "repair_required", False, "repair_batch"
    else:
        stage, terminal, next_action = "batching", False, "process_batch"
    result: dict[str, Any] = {
        "success": workspace.job.status != IngestionJobStatus.FAILED,
        "stage": stage,
        "terminal": terminal,
        "retryRequired": next_action == "repair_batch",
        "nextAction": next_action,
        "ingestionId": str(workspace.job.id),
        "documentId": str(workspace.document.id),
        "documentVersionId": str(workspace.version.id),
        "ontologyVersionId": str(workspace.version.ontology_version_id),
        "workspaceStats": {
            "chunks": len(workspace.chunks),
            "batches": len(workspace.batches),
            "stagedBatches": sum(item.status == "STAGED" for item in workspace.batches),
        },
        "resumed": resumed,
        "idempotent": idempotent,
    }
    if pending:
        result["nextBatch"] = {
            "batchIndex": pending.batch_index,
            "chunkIndexes": pending.chunk_indexes,
            "selectedScopeKeys": pending.scope_keys,
        }
    if workspace.job.error_message:
        result["errors"] = [
            {
                "code": "INGESTION_FAILED",
                "message": workspace.job.error_message,
                "location": workspace.job.error_stage,
            }
        ]
    return result


# ----------------------------------------------------------------------------
# Tên hàm: error_payload
# Chức năng:
#   - Tạo cấu trúc dữ liệu phản hồi lỗi tiêu chuẩn (terminal: True).
# Input:
#   - stage (str): Giai đoạn xảy ra lỗi.
#   - ingestion_id (str | None): Mã định danh phiên nạp tài liệu (nếu có).
#   - code (str): Mã lỗi định danh.
#   - message (str): Nội dung mô tả chi tiết lỗi.
# Output:
#   - dict[str, Any]: Dictionary lỗi chuẩn hóa.
# ----------------------------------------------------------------------------
def error_payload(
    stage: str,
    ingestion_id: str | None,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "success": False,
        "stage": stage,
        "terminal": True,
        "ingestionId": ingestion_id,
        "nextAction": None,
        "errors": [{"code": code, "message": message}],
    }


# ============================================================================
# 7. NHÓM TIỆN ÍCH NỘI BỘ (INTERNAL HELPERS)
# ============================================================================


# ----------------------------------------------------------------------------
# Tên hàm: _document_key
# Chức năng:
#   - Tạo khóa định danh tài liệu chuẩn hóa dạng kebab-case từ tên tệp tin.
# Input:
#   - filename (str): Tên tệp tin gốc.
# Output:
#   - str: Khóa định danh tài liệu chuẩn hóa.
# ----------------------------------------------------------------------------
def _document_key(filename: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", Path(filename).stem.casefold()).strip("-")
    return stem or hashlib.sha256(filename.encode("utf-8")).hexdigest()[:24]


# ----------------------------------------------------------------------------
# Tên hàm: _canonical_context
# Chức năng:
#   - Lấy danh sách các thực thể chuẩn hóa đã được staged ở các batch trước đó để làm ngữ cảnh tham chiếu chéo.
# Input:
#   - workspace (Workspace): Đối tượng Workspace hiện tại.
#   - before_batch (int): Chỉ số batch hiện tại (chỉ lấy thực thể staged trước batch này).
# Output:
#   - list[dict[str, Any]]: Danh sách tối đa 50 thực thể chuẩn hóa.
# ----------------------------------------------------------------------------
def _canonical_context(workspace: Workspace, before_batch: int) -> list[dict[str, Any]]:
    context = list(staged_entity_index(workspace, before_batch=before_batch).values())[
        :50
    ]
    logger.info(
        "CANONICAL_CONTEXT batchIndex=%s refs=%s stableKeys=%s",
        before_batch,
        len(context),
        [item["stableKey"] for item in context],
    )
    return context


canonical_context = _canonical_context


# ----------------------------------------------------------------------------
# Tên hàm: _chunks_from_evidence
# Chức năng:
#   - Tái tạo danh sách PreparedChunk tạm thời từ các trích dẫn bằng chứng (evidence) trong GraphPatchFragment.
# Input:
#   - fragment (GraphPatchFragment): Mảnh đồ thị cần trích xuất bằng chứng.
# Output:
#   - list[PreparedChunk]: Danh sách các PreparedChunk được tái tạo từ evidence.
# ----------------------------------------------------------------------------
def _chunks_from_evidence(fragment: GraphPatchFragment) -> list[PreparedChunk]:
    texts: dict[int, list[str]] = {}
    evidence_items = [e for node in fragment.nodes for e in node.evidence]
    evidence_items.extend(
        e for node in fragment.nodes for prop in node.properties for e in prop.evidence
    )
    evidence_items.extend(e for edge in fragment.edges for e in edge.evidence)
    for evidence in evidence_items:
        texts.setdefault(evidence.chunk_index, []).append(evidence.text)
    return [
        PreparedChunk(
            chunk_id=f"direct-{index}",
            chunk_index=index,
            text="\n".join(texts.get(index, [])),
            content_hash=hashlib.sha256(
                "\n".join(texts.get(index, [])).encode()
            ).hexdigest(),
            token_count=max(1, len("\n".join(texts.get(index, []))) // 4),
            source_anchor=f"direct-patch#chunk-{index}",
        )
        for index in sorted(
            {item.chunk_index for item in fragment.coverage} | set(texts)
        )
    ]
