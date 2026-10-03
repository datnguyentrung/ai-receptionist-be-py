"""Deterministic primitives exposed by the ingestion ADK tools.

The semantic control flow intentionally lives in the ingestion SKILL.md.
Phân định vai trò lưu trữ:
- PostgreSQL: Lưu trữ bền vững ontology/schema, compiled snapshots và schema proposals.
- RAM (IngestionRepository): Quản lý trạng thái workspace, job, batches và chunks trong tiến trình hiện tại.
- Neo4j: Lưu trữ bền vững đồ thị tri thức (Knowledge Graph) sau khi nạp chính thức.
"""

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.schemas.ingestion_schema import (
    GraphPatchFragment,
    IngestionJobStatus,
    PreparedChunk,
    SemanticGraphPatchFragment,
    SourceVersionStatus,
)
from app.services.ingestion.document.reader import DocumentReader
from app.services.ingestion.evidence_guard import GraphFragmentEvidenceGuard
from app.services.ingestion.graph_patch_compiler import GraphPatchCompiler
from app.services.ingestion.graph_store import Neo4jIngestionStore
from app.services.ingestion.ontology import (
    COMPILER_VERSION,
    OntologyCache,
    OntologyRegistry,
    validate_coverage_integrity,
)
from app.services.ingestion.repair_guard import RepairGuard
from app.services.ingestion.repository import (
    IngestionRepository,
    Workspace,
    staged_entity_index,
    workspace_chunks,
    workspace_fingerprint,
)
from app.services.ingestion.workflow_policy import evaluate_workflow

MAX_BATCH_VALIDATION_ATTEMPTS = max(
    1, int(os.getenv("INGESTION_MAX_BATCH_VALIDATION_ATTEMPTS", "2"))
)
logger = logging.getLogger(__name__)


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
) -> dict[str, Any]:
    """Chuẩn bị tài liệu qua DocumentReader và khởi tạo mới hoặc tái sử dụng workspace ingestion trong RAM."""
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
    workspace, resumed, committed = await repository.create_or_resume(
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
    return status_payload(workspace, resumed=resumed or committed, idempotent=committed)


async def get_batch(
    repository: IngestionRepository,
    ingestion_id: str,
    batch_index: int,
) -> dict[str, Any]:
    workspace = await required_workspace(repository, ingestion_id)
    guarded = workflow_guard_payload(workspace, "get_batch")
    if guarded is not None:
        return guarded
    batch = next(
        (item for item in workspace.batches if item.batch_index == batch_index), None
    )
    if batch is None:
        return error_payload(
            "batch_validation", ingestion_id, "INVALID_BATCH_INDEX", str(batch_index)
        )
    chunks = workspace_chunks(workspace, batch.chunk_indexes)
    logger.info(
        "BATCH_START ingestionId=%s batchIndex=%s chunkIndexes=%s",
        ingestion_id,
        batch_index,
        batch.chunk_indexes,
    )

    # Không chọn lại scope nếu đã chọn rồi.
    next_action = "load_scopes" if batch.scope_keys else "list_scopes"

    return {
        "success": True,
        "stage": "batch_retrieved",
        "terminal": False,
        "nextAction": next_action,
        "ingestionId": ingestion_id,
        "batch": {
            "batchIndex": batch.batch_index,
            "scopeHint": workspace.job.scope_hint,
            "selectedScopeKeys": batch.scope_keys,
            "chunks": [item.model_dump(by_alias=True, mode="json") for item in chunks],
            "canonicalGraphContext": _canonical_context(workspace, batch_index),
        },
    }


async def submit_batch(
    repository: IngestionRepository,
    ontology_cache: OntologyCache,
    ingestion_id: str,
    batch_index: int,
    scope_keys: list[str],
    semantic_fragment: SemanticGraphPatchFragment,
) -> dict[str, Any]:
    workspace = await required_workspace(repository, ingestion_id)
    guarded = workflow_guard_payload(workspace, "submit_batch")
    if guarded is not None:
        return guarded
    batch = next(
        (item for item in workspace.batches if item.batch_index == batch_index), None
    )
    if batch is None:
        return error_payload(
            "batch_validation", ingestion_id, "INVALID_BATCH_INDEX", str(batch_index)
        )

    if (
        batch.status == "REPAIR_REQUIRED"
        and batch.scope_keys
        and set(scope_keys) != set(batch.scope_keys)
    ):
        return error_payload(
            "batch_validation",
            ingestion_id,
            "REPAIR_SCOPE_MISMATCH",
            "Repair must reuse the previously selected scope set unless "
            "the workflow explicitly reselects scopes.",
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
        return batch_failure_payload(
            workspace, batch_index, issues, MAX_BATCH_VALIDATION_ATTEMPTS
        )

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
            "resolved" if fragment and edge_index < len(fragment.edges) else "unresolved",
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

    issues = [
        item.model_dump(by_alias=True, mode="json")
        for item in validation_issues
    ]
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
        result = batch_failure_payload(
            workspace, batch_index, issues, MAX_BATCH_VALIDATION_ATTEMPTS
        )
        if result["terminal"]:
            logger.error(
                "TERMINAL_FAILURE ingestionId=%s batchIndex=%s attempt=%s reason=retry_limit",
                ingestion_id,
                batch_index,
                result.get("attempt"),
            )
        if not result["terminal"]:
            codes = {item.get("code") for item in issues}
            if codes & {
                "SCHEMA_GAP_CANDIDATE",
                "UNKNOWN_ENTITY_TYPE",
                "UNKNOWN_PROPERTY",
                "UNKNOWN_RELATIONSHIP",
            }:
                result["stage"] = "schema_gap_candidate"
                result["nextAction"] = "assess_schema_gap"
                result["retryRequired"] = False
            elif "MISSING_SCOPE" in codes:
                result["stage"] = "scope_reselection_required"
                result["nextAction"] = "reselect_scopes"
                result["retryRequired"] = True
        return result

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
    result = status_payload(workspace)
    result["submittedBatchIndex"] = batch_index
    result["scopeKeys"] = projection.scope_keys
    result["mergedSchemaHash"] = projection.digest
    logger.info(
        "BATCH_STAGED ingestionId=%s batchIndex=%s canonicalNodes=%s edges=%s facts=%s",
        ingestion_id,
        batch_index,
        len(fragment.nodes),
        len(fragment.edges),
        sum(len(node.properties) for node in fragment.nodes) + len(fragment.edges),
    )
    return result


async def finalize(
    repository: IngestionRepository,
    ingestion_id: str,
) -> dict[str, Any]:
    workspace = await required_workspace(repository, ingestion_id)
    guarded = workflow_guard_payload(workspace, "finalize")
    if guarded is not None:
        return guarded
    incomplete = [
        item.batch_index for item in workspace.batches if item.status != "STAGED"
    ]
    if incomplete:
        blocked = [
            item.batch_index
            for item in workspace.batches
            if item.status == "BLOCKED_SCHEMA"
        ]
        return {
            "success": False,
            "stage": "awaiting_schema_approval" if blocked else "repair_required",
            "terminal": False,
            "nextAction": "wait_for_schema_review" if blocked else "repair_batches",
            "ingestionId": ingestion_id,
            "repairBatchIndexes": incomplete,
            "errors": [
                {
                    "code": "INCOMPLETE_BATCHES",
                    "message": f"Batches require schema review: {blocked}"
                    if blocked
                    else f"Batches require repair: {incomplete}",
                    "location": "batches",
                    "retryable": True,
                }
            ],
        }

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
        flattened = [
            {**issue, "batchIndex": batch_index}
            for batch_index, issues in coverage_errors.items()
            for issue in issues
        ]
        has_schema_gap = any(
            item.get("code") == "SCHEMA_GAP_CANDIDATE" for item in flattened
        )
        return {
            "success": False,
            "stage": "schema_gap_candidate" if has_schema_gap else "repair_required",
            "terminal": False,
            "retryRequired": not has_schema_gap,
            "nextAction": "assess_schema_gap" if has_schema_gap else "repair_batches",
            "ingestionId": ingestion_id,
            "repairBatchIndexes": sorted(coverage_errors),
            "errors": flattened,
        }

    return status_payload(
        await repository.mark_ready(ingestion_id, workspace_fingerprint(workspace))
    )


async def fill(
    repository: IngestionRepository,
    graph_store: Neo4jIngestionStore,
    ingestion_id: str,
) -> dict[str, Any]:
    """Ghi chính thức tri thức vào Neo4j và cập nhật trạng thái COMMITTED cho workspace trong bộ nhớ RAM."""
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


async def _classify_missing_scopes(
    ontology_cache: OntologyCache,
    ontology_version_id: str,
    selected_projection,
    semantic: SemanticGraphPatchFragment,
    issues: list[dict[str, Any]],
    *,
    external_node_types: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Classify ontology errors by introspecting the pinned ontology version.

    The classifier is data-driven: it never knows domain-specific relationship names.
    It distinguishes model mapping errors, omitted scopes, and genuine schema gaps.
    """
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
            item.scope_key for item in catalog
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
                if (
                    other_class
                    and other_registry.resolve_property_name(
                        other_class, fact.property_name
                    )
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


def _compatible_relationships(
    relationships: list[dict[str, Any]],
    source_type: str,
    target_type: str,
) -> list[str]:
    return sorted({
        item["technicalName"]
        for item in relationships
        if item.get("sourceEntityType") == source_type
        and item.get("targetEntityType") == target_type
        and item.get("technicalName")
    })


async def required_workspace(
    repository: IngestionRepository, ingestion_id: str
) -> Workspace:
    workspace = await repository.get_workspace(ingestion_id)
    if workspace is None:
        raise KeyError(f"Unknown ingestionId: {ingestion_id}")
    return workspace


def workflow_guard_payload(
    workspace: Workspace,
    action: str,
) -> dict[str, Any] | None:
    """Return a structured state payload when an action is illegal in the current job state."""
    decision = evaluate_workflow(workspace.job.status, action)
    if decision.allowed:
        return None
    result = status_payload(workspace)
    result["blockedAction"] = action
    if decision.next_action is not None:
        result["nextAction"] = decision.next_action
    return result


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


def _document_key(filename: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", Path(filename).stem.casefold()).strip("-")
    return stem or hashlib.sha256(filename.encode("utf-8")).hexdigest()[:24]


def _canonical_context(workspace: Workspace, before_batch: int) -> list[dict[str, Any]]:
    context = list(staged_entity_index(workspace, before_batch=before_batch).values())[:50]
    logger.info(
        "CANONICAL_CONTEXT batchIndex=%s refs=%s stableKeys=%s",
        before_batch,
        len(context),
        [item["stableKey"] for item in context],
    )
    return context


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


__all__ = [
    "begin",
    "delete_document",
    "error_payload",
    "fill",
    "finalize",
    "get_batch",
    "list_scopes",
    "load_scope",
    "required_workspace",
    "rollback_version",
    "status_payload",
    "submit_batch",
    "validate_patch",
]
