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
    ChunkLedger,
    EdgeClaimMapping,
    GraphPatchFragment,
    IngestionJobStatus,
    PreparedChunk,
    PropertyClaimMapping,
    SemanticBatchExtraction,
    SemanticClaim,
    SemanticEntity,
    SemanticGraphPatchFragment,
    SemanticGraphRepairDelta,
    SourceVersionStatus,
    ValidationIssue,
)
from app.services.ingestion.batch_claim_compiler import (
    BatchClaimCompiler,
)
from app.services.ingestion.document.reader import DocumentReader
from app.services.ingestion.evidence_guard import (
    EvidenceGroundingEngine,
    GraphFragmentEvidenceGuard,
)
from app.services.ingestion.fact_references import (
    add_local_fact_aliases,
    build_fact_index,
    canonical_json,
)
from app.services.ingestion.graph_patch_compiler import GraphPatchCompiler
from app.services.ingestion.graph_store import Neo4jIngestionStore
from app.services.ingestion.ontology import (
    COMPILER_VERSION,
    OntologyCache,
    OntologyRegistry,
)
from app.services.ingestion.readiness import (
    ReadinessResult,
    WorkspaceReadinessValidator,
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
    grounding = EvidenceGroundingEngine()
    logger.info(
        "BATCH_START ingestionId=%s batchIndex=%s chunkIndexes=%s",
        ingestion_id,
        batch_index,
        batch.chunk_indexes,
    )

    if batch.status == "REPAIR_REQUIRED":
        next_action = "repair_batch_delta"
    else:
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
            "chunks": [
                {
                    **item.model_dump(by_alias=True, mode="json"),
                    "evidenceUnits": [
                        unit.model_dump(by_alias=True, mode="json")
                        for unit in grounding.build_units(item)
                    ],
                }
                for item in chunks
            ],
            "canonicalGraphContext": _canonical_context(workspace, batch_index),
            "canonicalFactContext": _canonical_fact_context(
                workspace, batch_index, include_repair_baseline=True
            ),
            "repairContext": _repair_context(workspace, batch_index),
        },
    }


def ensure_semantic_batch_extraction(
    payload: Any,
    batch_chunk_indexes: list[int],
) -> SemanticBatchExtraction:
    """Canonicalize incoming extraction payload into SemanticBatchExtraction."""
    if isinstance(payload, SemanticBatchExtraction):
        return payload
    if isinstance(payload, dict) and "chunks" in payload:
        return SemanticBatchExtraction.model_validate(payload)

    # Legacy SemanticGraphPatchFragment conversion
    fragment = (
        payload
        if isinstance(payload, SemanticGraphPatchFragment)
        else SemanticGraphPatchFragment.model_validate(payload)
    )

    entities = [
        SemanticEntity(
            temp_id=node.temp_id,
            class_name=node.class_name,
            entity_ref=node.entity_ref,
        )
        for node in fragment.nodes
    ]

    claims_by_chunk: dict[int, list[SemanticClaim]] = {idx: [] for idx in batch_chunk_indexes}
    no_fact_reasons: dict[int, str] = {}

    for cov in fragment.coverage:
        if cov.decision in {"NO_RELEVANT_FACT", "NOT_RELEVANT"}:
            no_fact_reasons[cov.chunk_index] = cov.reason or "No relevant facts identified"
        for dup in getattr(cov, "duplicate_claims", []):
            claims_by_chunk.setdefault(cov.chunk_index, []).append(
                SemanticClaim(
                    claim_id=f"dup_{cov.chunk_index}_{dup.fact_ref}",
                    statement=f"Duplicate fact {dup.fact_ref}",
                    evidence=dup.evidence,
                    outcome="DUPLICATE",
                    fact_ref=dup.fact_ref,
                )
            )

    claim_counter = 0
    for node in fragment.nodes:
        for prop in node.properties:
            for ev in prop.evidence:
                claim_counter += 1
                claim_id = f"c_{claim_counter}_{node.temp_id}_{prop.property_name}"
                c = SemanticClaim(
                    claim_id=claim_id,
                    statement=f"{node.class_name} {prop.property_name} is {prop.value}",
                    evidence=ev,
                    outcome="MAPPED",
                    mapping=PropertyClaimMapping(
                        entity_ref=node.temp_id,
                        property_name=prop.property_name,
                        value=prop.value,
                    ),
                )
                claims_by_chunk.setdefault(ev.chunk_index, []).append(c)

    for edge in fragment.edges:
        for ev in edge.evidence:
            claim_counter += 1
            claim_id = f"c_{claim_counter}_{edge.edge_name}"
            c = SemanticClaim(
                claim_id=claim_id,
                statement=f"{edge.edge_name} from {edge.source_temp_id} to {edge.target_temp_id}",
                evidence=ev,
                outcome="MAPPED",
                mapping=EdgeClaimMapping(
                    edge_name=edge.edge_name,
                    source_ref=edge.source_temp_id,
                    target_ref=edge.target_temp_id,
                    properties=edge.properties or {},
                ),
            )
            claims_by_chunk.setdefault(ev.chunk_index, []).append(c)

    if not fragment.nodes and not fragment.edges:
        for cov in fragment.coverage:
            if cov.decision in {"MAPPED", "PARTIALLY_MAPPED"}:
                raise ValueError(f"Chunk {cov.chunk_index} claimed {cov.decision} in legacy coverage but has no mapped properties or edges")

    chunks = [
        ChunkLedger(
            chunk_index=idx,
            claims=claims_by_chunk.get(idx, []),
            no_relevant_fact_reason=no_fact_reasons.get(idx) or ("No relevant facts" if not claims_by_chunk.get(idx) else None),
        )
        for idx in batch_chunk_indexes
    ]

    return SemanticBatchExtraction(
        entities=entities,
        chunks=chunks,
        warnings=fragment.warnings,
    )


async def submit_batch(
    repository: IngestionRepository,
    ontology_cache: OntologyCache,
    ingestion_id: str,
    batch_index: int,
    scope_keys: list[str],
    semantic_fragment: SemanticBatchExtraction | SemanticGraphPatchFragment | dict[str, Any],
) -> dict[str, Any]:
    """Single semantic extraction submission per batch using the Claim Ledger compiler."""
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

    if batch.status in {"BLOCKED_SCHEMA", "SCHEMA_REJECTED"}:
        return {
            "success": False,
            "stage": "schema_review_required",
            "terminal": False,
            "retryRequired": False,
            "nextAction": "review_schema_proposal",
            "ingestionId": ingestion_id,
            "batchIndex": batch_index,
            "errors": [
                {
                    "code": "BATCH_GATED_BY_SCHEMA_REVIEW",
                    "message": f"Batch {batch_index} is awaiting schema review",
                    "retryable": False,
                }
            ],
        }

    logger.info(
        "BATCH_EXTRACTION_STARTED ingestionId=%s batchIndex=%s scopes=%s",
        ingestion_id,
        batch_index,
        scope_keys,
    )

    try:
        extraction = ensure_semantic_batch_extraction(
            semantic_fragment, list(batch.chunk_indexes)
        )
    except (ValidationError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            issues = [
                {
                    "code": "INVALID_BATCH_EXTRACTION_SCHEMA",
                    "message": item["msg"],
                    "location": ".".join(str(part) for part in item["loc"]),
                    "retryable": False,
                }
                for item in exc.errors()
            ]
        else:
            issues = [
                {
                    "code": "MAPPED_WITHOUT_MAPPING",
                    "message": str(exc),
                    "location": "coverage",
                    "retryable": False,
                }
            ]
        await repository.store_batch_result(
            ingestion_id,
            batch_index,
            [],
            "",
            None,
            None,
            issues,
            max_attempts=MAX_BATCH_VALIDATION_ATTEMPTS,
            status="EXTRACTION_REJECTED",
        )
        logger.error(
            "EXTRACTION_REJECTED ingestionId=%s batchIndex=%s errors=%s",
            ingestion_id,
            batch_index,
            json.dumps(issues, ensure_ascii=False),
        )
        return {
            "success": False,
            "stage": "extraction_rejected",
            "terminal": True,
            "retryRequired": False,
            "nextAction": "report_extraction_failure",
            "ingestionId": ingestion_id,
            "batchIndex": batch_index,
            "semanticSubmissions": 1,
            "errors": issues,
        }

    logger.info(
        "CLAIM_LEDGER_RECEIVED ingestionId=%s batchIndex=%s entities=%s chunks=%s claims=%s",
        ingestion_id,
        batch_index,
        len(extraction.entities),
        len(extraction.chunks),
        sum(len(c.claims) for c in extraction.chunks),
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

    chunks = workspace_chunks(workspace, batch.chunk_indexes)
    staged_context = {
        "staged_entities": staged_entity_index(workspace, before_batch=batch_index),
        "staged_facts": build_fact_index(_staged_fragments(workspace, before_batch=batch_index)),
    }

    compiler = BatchClaimCompiler()
    compile_result = compiler.compile(
        extraction=extraction,
        ontology_projection=projection,
        staged_context=staged_context,
        source_chunks=chunks,
    )

    logger.info(
        "CLAIM_COMPILATION_RESULT ingestionId=%s batchIndex=%s errors=%s gaps=%s warnings=%s",
        ingestion_id,
        batch_index,
        len(compile_result.errors),
        len(compile_result.schema_gaps),
        len(compile_result.warnings),
    )

    ledger_dict = extraction.model_dump(by_alias=True, mode="json")

    # 1. Hard validation errors -> EXTRACTION_REJECTED
    if compile_result.errors:
        workspace = await repository.store_batch_result(
            ingestion_id,
            batch_index,
            bindings,
            projection.digest,
            ledger_dict,
            None,
            compile_result.errors,
            max_attempts=MAX_BATCH_VALIDATION_ATTEMPTS,
            status="EXTRACTION_REJECTED",
            claim_ledger=ledger_dict,
        )
        logger.error(
            "EXTRACTION_REJECTED ingestionId=%s batchIndex=%s errors=%s",
            ingestion_id,
            batch_index,
            json.dumps(compile_result.errors, ensure_ascii=False),
        )
        return {
            "success": False,
            "stage": "extraction_rejected",
            "terminal": True,
            "retryRequired": False,
            "nextAction": "report_extraction_failure",
            "ingestionId": ingestion_id,
            "batchIndex": batch_index,
            "semanticSubmissions": 1,
            "errors": compile_result.errors,
        }

    # 2. Schema review required -> SCHEMA_REVIEW_REQUIRED
    if compile_result.schema_gaps:
        workspace = await repository.block_batch_for_proposal(
            ingestion_id,
            batch_index,
            [
                {
                    "code": "SCHEMA_REVIEW_REQUIRED",
                    "message": f"Batch {batch_index} contains {len(compile_result.schema_gaps)} schema gap(s) requiring review",
                    "location": f"batch[{batch_index}]",
                    "retryable": False,
                }
            ],
            claim_ledger=ledger_dict,
            schema_gaps=compile_result.schema_gaps,
        )
        logger.info(
            "SCHEMA_REVIEW_REQUIRED ingestionId=%s batchIndex=%s gaps=%s",
            ingestion_id,
            batch_index,
            len(compile_result.schema_gaps),
        )
        return {
            "success": True,
            "stage": "schema_review_required",
            "terminal": False,
            "retryRequired": False,
            "nextAction": "review_schema_proposal",
            "ingestionId": ingestion_id,
            "batchIndex": batch_index,
            "semanticSubmissions": 1,
            "schemaGaps": compile_result.schema_gaps,
        }

    # 3. Valid extraction -> FIRST_PASS_STAGED
    workspace = await repository.store_batch_result(
        ingestion_id,
        batch_index,
        bindings,
        projection.digest,
        ledger_dict,
        compile_result.fragment,
        [],
        max_attempts=MAX_BATCH_VALIDATION_ATTEMPTS,
        status="STAGED",
        claim_ledger=ledger_dict,
    )
    result = status_payload(workspace)
    result["success"] = True
    result["stage"] = "batch_staged"
    result["terminal"] = False
    result["retryRequired"] = False
    result["semanticSubmissions"] = 1
    remaining = [
        b for b in workspace.batches
        if b.batch_index > batch_index and b.status != "STAGED"
    ]
    result["nextAction"] = "process_next_batch" if remaining else "finalize"
    result["submittedBatchIndex"] = batch_index
    result["scopeKeys"] = projection.scope_keys
    result["mergedSchemaHash"] = projection.digest
    logger.info(
        "BATCH_FIRST_PASS_STAGED ingestionId=%s batchIndex=%s nodes=%s edges=%s",
        ingestion_id,
        batch_index,
        len(compile_result.fragment.nodes) if compile_result.fragment else 0,
        len(compile_result.fragment.edges) if compile_result.fragment else 0,
    )
    return result


async def recompile_batch_from_ledger(
    repository: IngestionRepository,
    ontology_cache: OntologyCache,
    ingestion_id: str,
    batch_index: int,
    approved_proposals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Recompile stored claim ledger against updated ontology without re-prompting LLM."""
    workspace = await required_workspace(repository, ingestion_id)
    batch = next((b for b in workspace.batches if b.batch_index == batch_index), None)
    if batch is None:
        raise KeyError(f"Batch {batch_index} not found in workspace {ingestion_id}")
    if not batch.claim_ledger:
        raise RuntimeError(f"Batch {batch_index} has no stored claim ledger for recompile")

    ledger_data = json.loads(json.dumps(batch.claim_ledger))
    approved_names = {
        p.get("technicalName") or (p.get("payload") or {}).get("technicalName")
        for p in (approved_proposals or [])
    }
    for chunk in ledger_data.get("chunks", []):
        for claim in chunk.get("claims", []):
            gap = claim.get("schemaGap")
            if claim.get("outcome") == "SCHEMA_GAP" and gap:
                tech_name = gap.get("technicalName")
                if not approved_names or tech_name in approved_names:
                    claim["outcome"] = "MAPPED"
                    if gap.get("kind") == "PROPERTY":
                        claim["mapping"] = {
                            "kind": "PROPERTY",
                            "entityRef": gap.get("entityRef"),
                            "propertyName": tech_name,
                            "value": gap.get("value"),
                        }
                    elif gap.get("kind") == "RELATIONSHIP":
                        claim["mapping"] = {
                            "kind": "EDGE",
                            "edgeName": tech_name,
                            "sourceRef": gap.get("sourceRef"),
                            "targetRef": gap.get("targetRef"),
                            "properties": {},
                        }
                    claim["schemaGap"] = None

    scope_keys = list(batch.scope_keys)
    if not scope_keys and hasattr(ontology_cache, "list_scopes"):
        catalog = await ontology_cache.list_scopes(str(workspace.job.ontology_version_id))
        scope_keys = [item.scope_key for item in catalog]
    projection = await ontology_cache.get_many(
        scope_keys, str(workspace.job.ontology_version_id)
    )
    chunks = workspace_chunks(workspace, batch.chunk_indexes)
    staged_context = {
        "staged_entities": staged_entity_index(workspace, before_batch=batch_index),
        "staged_facts": build_fact_index(_staged_fragments(workspace, before_batch=batch_index)),
    }
    extraction = SemanticBatchExtraction.model_validate(ledger_data)
    compiler = BatchClaimCompiler()
    compile_result = compiler.compile(
        extraction=extraction,
        ontology_projection=projection,
        staged_context=staged_context,
        source_chunks=chunks,
    )
    if compile_result.errors:
        return {
            "success": False,
            "stage": "extraction_rejected",
            "errors": compile_result.errors,
        }
    if compile_result.schema_gaps:
        return {
            "success": True,
            "stage": "schema_review_required",
            "schemaGaps": compile_result.schema_gaps,
        }

    bindings = [
        {"scopeKey": k, "schemaHash": (await ontology_cache.get(k, str(workspace.job.ontology_version_id))).digest}
        for k in projection.scope_keys
    ]
    workspace = await repository.store_batch_result(
        ingestion_id,
        batch_index,
        bindings,
        projection.digest,
        extraction.model_dump(by_alias=True, mode="json"),
        compile_result.fragment,
        [],
        max_attempts=MAX_BATCH_VALIDATION_ATTEMPTS,
        status="STAGED",
        claim_ledger=extraction.model_dump(by_alias=True, mode="json"),
    )
    result = status_payload(workspace)
    result["stage"] = "batch_staged"
    result["success"] = True
    return result



async def repair_batch(
    repository: IngestionRepository,
    ontology_cache: OntologyCache,
    ingestion_id: str,
    batch_index: int,
    scope_keys: list[str],
    repair_delta: SemanticGraphRepairDelta,
) -> dict[str, Any]:
    """Apply an additive semantic delta to a protected batch baseline."""

    workspace = await required_workspace(repository, ingestion_id)
    batch = next(
        (item for item in workspace.batches if item.batch_index == batch_index), None
    )
    if batch is None:
        return error_payload(
            "batch_validation", ingestion_id, "INVALID_BATCH_INDEX", str(batch_index)
        )
    if batch.status != "REPAIR_REQUIRED":
        return error_payload(
            "batch_validation",
            ingestion_id,
            "REPAIR_NOT_REQUIRED",
            f"Batch {batch_index} is {batch.status}, not REPAIR_REQUIRED",
        )
    if batch.scope_keys and set(scope_keys) != set(batch.scope_keys):
        return {
            "success": False,
            "stage": "repair_required",
            "terminal": False,
            "retryRequired": True,
            "nextAction": "repair_batch_delta",
            "ingestionId": ingestion_id,
            "batchIndex": batch_index,
            "errors": [
                {
                    "code": "REPAIR_SCOPE_MISMATCH",
                    "message": (
                        "Repair must reuse the scope set selected by the original "
                        "submission"
                    ),
                    "retryable": True,
                }
            ],
            "repairContext": _repair_context(workspace, batch_index),
        }
    baseline_data = getattr(batch, "validated_baseline", None)
    baseline = (
        GraphPatchFragment.model_validate(baseline_data)
        if baseline_data
        else GraphPatchFragment(
            ontology_version=str(workspace.job.ontology_version_id),
            nodes=[],
            edges=[],
            coverage=[],
        )
    )
    expected_fingerprint = _baseline_fingerprint(baseline if baseline_data else None)
    if repair_delta.baseline_fingerprint != expected_fingerprint:
        return {
            "success": False,
            "stage": "repair_required",
            "terminal": False,
            "retryRequired": True,
            "nextAction": "repair_batch_delta",
            "ingestionId": ingestion_id,
            "batchIndex": batch_index,
            "errors": [
                {
                    "code": "STALE_REPAIR_BASELINE",
                    "message": "Repair delta does not match the current protected baseline",
                    "retryable": True,
                }
            ],
            "repairContext": _repair_context(workspace, batch_index),
        }

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
    semantic_delta = SemanticGraphPatchFragment(
        nodes=repair_delta.nodes,
        edges=repair_delta.edges,
        coverage=repair_delta.coverage,
        warnings=repair_delta.warnings,
    )
    registry = OntologyRegistry(projection)
    canonical_semantic = registry.canonicalize_semantic_fragment(semantic_delta)
    entity_index = staged_entity_index(workspace, before_batch=batch_index)
    for node in baseline.nodes:
        entity_index[f"entity:{node.temp_id}"] = {
            "ref": f"entity:{node.temp_id}",
            "stableKey": node.temp_id,
            "className": node.class_name,
            "identity": node.identity,
            "displayProperties": node.identity,
        }
    compile_result = GraphPatchCompiler().compile(
        canonical_semantic,
        projection,
        staged_entities=entity_index,
    )
    validation_issues = list(compile_result.issues)
    candidate = baseline.model_copy(deep=True)
    chunks = workspace_chunks(workspace, batch.chunk_indexes)

    if compile_result.fragment is not None:
        delta_fragment = GraphFragmentEvidenceGuard().canonicalize(
            compile_result.fragment, chunks
        )
        edge_conflicts = _repair_edge_property_conflicts(baseline, delta_fragment)
        candidate = RepairGuard.merge_baselines(baseline, delta_fragment) or candidate
        candidate.warnings = list(
            dict.fromkeys([*baseline.warnings, *repair_delta.warnings])
        )
        validation_issues.extend(_repair_property_conflicts(candidate, projection))
        validation_issues.extend(edge_conflicts)
        fact_fragments = _staged_fragments(workspace, before_batch=batch_index)
        fact_fragments.append(candidate)
        available_facts = build_fact_index(fact_fragments)
        add_local_fact_aliases(available_facts, canonical_semantic, delta_fragment)
        external_types = {
            item["stableKey"]: item["className"] for item in entity_index.values()
        }
        validation_issues.extend(registry.validate_fragment(
            candidate,
            chunks,
            external_node_types=external_types,
            available_facts=available_facts,
            artifact_name=getattr(workspace.document, "name", None),
        ))

    issues = [
        item.model_dump(by_alias=True, mode="json") for item in validation_issues
    ]
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
        issue_codes = {item.get("code") for item in issues}
        count_attempt = not bool(
            issue_codes & {"COVERAGE_REVIEW_REQUIRED", "SCHEMA_GAP_CANDIDATE"}
        )
        extracted = RepairGuard.extract_validated_baseline(candidate, validation_issues)
        new_baseline = RepairGuard.merge_baselines(baseline, extracted)
        workspace = await repository.store_batch_result(
            ingestion_id,
            batch_index,
            bindings,
            projection.digest,
            semantic_delta,
            candidate,
            issues,
            max_attempts=MAX_BATCH_VALIDATION_ATTEMPTS,
            validated_baseline=new_baseline,
            count_attempt=count_attempt,
        )
        result = batch_failure_payload(
            workspace, batch_index, issues, MAX_BATCH_VALIDATION_ATTEMPTS
        )
        if not result["terminal"]:
            if issue_codes & {
                "SCHEMA_GAP_CANDIDATE",
                "UNKNOWN_ENTITY_TYPE",
                "UNKNOWN_PROPERTY",
                "UNKNOWN_RELATIONSHIP",
            }:
                result["stage"] = "schema_gap_candidate"
                result["nextAction"] = "assess_schema_gap"
                result["retryRequired"] = False
            elif "MISSING_SCOPE" in issue_codes:
                result["stage"] = "scope_reselection_required"
                result["nextAction"] = "reselect_scopes"
                result["retryRequired"] = True
        return result

    workspace = await repository.store_batch_result(
        ingestion_id,
        batch_index,
        bindings,
        projection.digest,
        semantic_delta,
        candidate,
        [],
        max_attempts=MAX_BATCH_VALIDATION_ATTEMPTS,
        validated_baseline=candidate,
        count_attempt=False,
    )
    result = status_payload(workspace)
    result["submittedBatchIndex"] = batch_index
    result["repairApplied"] = True
    return result


def _repair_property_conflicts(
    fragment: GraphPatchFragment, projection: Any
) -> list[Any]:
    issues = []
    multi_value = {
        (item["entityType"], item["technicalName"]): bool(item.get("multiValue"))
        for item in projection.properties
    }
    for node_index, node in enumerate(fragment.nodes):
        values: dict[str, str] = {}
        for property_index, fact in enumerate(node.properties):
            encoded = canonical_json(fact.value)
            previous = values.get(fact.property_name)
            if (
                previous is not None
                and previous != encoded
                and not multi_value.get((node.class_name, fact.property_name), False)
            ):
                issues.append(
                    ValidationIssue(
                        code="REPAIR_PROPERTY_CONFLICT",
                        message=(
                            f"Repair adds a conflicting value for {fact.property_name}"
                        ),
                        location=f"nodes.{node_index}.properties.{property_index}.value",
                        retryable=True,
                    )
                )
            values[fact.property_name] = encoded
    return issues


def _repair_edge_property_conflicts(
    baseline: GraphPatchFragment, delta: GraphPatchFragment
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    baseline_edges = {
        (edge.edge_name, edge.source_temp_id, edge.target_temp_id): (index, edge)
        for index, edge in enumerate(baseline.edges)
    }
    for edge in delta.edges:
        matched = baseline_edges.get(
            (edge.edge_name, edge.source_temp_id, edge.target_temp_id)
        )
        if matched is None:
            continue
        edge_index, existing = matched
        for name, value in edge.properties.items():
            if name in existing.properties and canonical_json(
                existing.properties[name]
            ) != canonical_json(value):
                issues.append(
                    ValidationIssue(
                        code="REPAIR_EDGE_PROPERTY_CONFLICT",
                        message=f"Repair changes edge property {name}",
                        location=f"edges.{edge_index}.properties.{name}",
                        retryable=True,
                    )
                )
    return issues


async def finalize(
    repository: IngestionRepository,
    ontology_cache: OntologyCache,
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

    projection = await _workspace_projection(ontology_cache, workspace)
    readiness = WorkspaceReadinessValidator().validate(workspace, projection)
    if readiness.issues:
        return await _block_on_readiness(repository, workspace, readiness)

    return status_payload(await repository.mark_ready(
        ingestion_id,
        workspace_fingerprint(workspace, merged_graph_digest=readiness.digest),
    ))


async def fill(
    repository: IngestionRepository,
    ontology_cache: OntologyCache,
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
    projection = await _workspace_projection(ontology_cache, workspace)
    readiness = WorkspaceReadinessValidator().validate(workspace, projection)
    if readiness.issues:
        return await _block_on_readiness(repository, workspace, readiness)
    expected = workspace_fingerprint(workspace, merged_graph_digest=readiness.digest)
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
        result = await graph_store.fill(workspace, readiness.fragment)
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


async def _workspace_projection(
    ontology_cache: OntologyCache, workspace: Workspace
):
    scope_keys = sorted(
        {scope for batch in workspace.batches for scope in batch.scope_keys}
    )
    if not scope_keys:
        raise RuntimeError("Cannot validate readiness without pinned ontology scopes")
    return await ontology_cache.get_many(
        scope_keys, str(workspace.job.ontology_version_id)
    )


async def _block_on_readiness(
    repository: IngestionRepository,
    workspace: Workspace,
    readiness: ReadinessResult,
) -> dict[str, Any]:
    serialized: dict[int, list[dict[str, Any]]] = {
        batch_index: [issue.model_dump(by_alias=True, mode="json") for issue in issues]
        for batch_index, issues in readiness.issues_by_batch.items()
    }
    for batch_index, issues in serialized.items():
        batch = next(item for item in workspace.batches if item.batch_index == batch_index)
        baseline = None
        if batch.graph_fragment:
            try:
                baseline = RepairGuard.extract_validated_baseline(
                    GraphPatchFragment.model_validate(batch.graph_fragment), issues
                )
            except ValidationError:
                baseline = None
        await repository.mark_batch_for_repair(
            str(workspace.job.id),
            batch_index,
            issues,
            validated_baseline=baseline,
        )
    flattened = [
        {**issue, "batchIndex": batch_index}
        for batch_index, issues in serialized.items()
        for issue in issues
    ]
    codes = {item["code"] for item in flattened}
    if "SCHEMA_GAP_CANDIDATE" in codes:
        stage, next_action, retry = "schema_gap_candidate", "assess_schema_gap", False
    elif "COVERAGE_REVIEW_REQUIRED" in codes:
        stage, next_action, retry = "coverage_review_required", "request_coverage_review", False
    elif "SCALAR_PROPERTY_CONFLICT" in codes:
        stage, next_action, retry = "semantic_conflict", "repair_batches", True
    else:
        stage, next_action, retry = "repair_required", "repair_batches", True
    return {
        "success": False,
        "stage": stage,
        "terminal": False,
        "retryRequired": retry,
        "nextAction": next_action,
        "ingestionId": str(workspace.job.id),
        "repairBatchIndexes": sorted(serialized),
        "errors": flattened,
    }


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
    codes = {item.get("code") for item in issues}
    if "SCHEMA_GAP_CANDIDATE" in codes:
        stage, retry_required, next_action = (
            "schema_gap_candidate",
            False,
            "assess_schema_gap",
        )
    elif "COVERAGE_REVIEW_REQUIRED" in codes:
        stage, retry_required, next_action = (
            "coverage_review_required",
            False,
            "request_coverage_review",
        )
    else:
        stage, retry_required, next_action = (
            "repair_required",
            True,
            "repair_batch_delta",
        )
    return {
        "success": False,
        "stage": stage,
        "terminal": False,
        "retryRequired": retry_required,
        "nextAction": next_action,
        "ingestionId": str(workspace.job.id),
        "batchIndex": batch_index,
        "scopeKeys": getattr(batch, "scope_keys", []),
        "affectedChunkIndexes": list(getattr(batch, "chunk_indexes", [])),
        "errors": issues,
        "validationAttempts": getattr(batch, "validation_attempts", 0),
        "maxAttempts": max_attempts,
        "repairContext": _repair_context(workspace, batch_index),
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
        stage, terminal, next_action = (
            "repair_required",
            False,
            "repair_batch_delta",
        )
    else:
        stage, terminal, next_action = "batching", False, "process_batch"
    result: dict[str, Any] = {
        "success": workspace.job.status != IngestionJobStatus.FAILED,
        "stage": stage,
        "terminal": terminal,
        "retryRequired": next_action == "repair_batch_delta",
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
    staged_entities = staged_entity_index(workspace)
    result["stagedEntitiesSummary"] = [
        {
            "className": item["className"],
            "stableKey": item["stableKey"],
            "identity": item.get("identity", {}),
            "displayProperties": item.get("displayProperties", {}),
        }
        for item in staged_entities.values()
    ]
    pending_gaps = [
        gap
        for batch in workspace.batches
        for gap in (getattr(batch, "schema_gaps", None) or [])
    ]
    if pending_gaps:
        result["pendingSchemaGaps"] = pending_gaps
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


def _staged_fragments(
    workspace: Workspace, before_batch: int | None = None
) -> list[GraphPatchFragment]:
    fragments: list[GraphPatchFragment] = []
    for batch in workspace.batches:
        if before_batch is not None and batch.batch_index >= before_batch:
            continue
        if batch.status != "STAGED" or not batch.graph_fragment:
            continue
        fragments.append(GraphPatchFragment.model_validate(batch.graph_fragment))
    return fragments


def _canonical_fact_context(
    workspace: Workspace,
    before_batch: int,
    *,
    include_repair_baseline: bool = False,
) -> list[dict[str, Any]]:
    fragments = _staged_fragments(workspace, before_batch)
    if include_repair_baseline:
        batch = next(
            (item for item in workspace.batches if item.batch_index == before_batch),
            None,
        )
        if batch and batch.validated_baseline:
            fragments.append(GraphPatchFragment.model_validate(batch.validated_baseline))
    return list(build_fact_index(fragments).values())[:100]


def _repair_context(workspace: Workspace, batch_index: int) -> dict[str, Any] | None:
    batch = next(
        (item for item in workspace.batches if item.batch_index == batch_index), None
    )
    if batch is None or batch.status != "REPAIR_REQUIRED":
        return None
    baseline_data = getattr(batch, "validated_baseline", None)
    baseline = (
        GraphPatchFragment.model_validate(baseline_data) if baseline_data else None
    )
    covered_indexes = (
        {item.chunk_index for item in baseline.coverage} if baseline else set()
    )
    unresolved_indexes = sorted(set(batch.chunk_indexes) - covered_indexes)
    fingerprint = _baseline_fingerprint(baseline)
    return {
        "mode": "ADDITIVE_DELTA",
        "baselineFingerprint": fingerprint,
        "validationIssues": getattr(batch, "validation_issues", []),
        "unresolvedChunkIndexes": unresolved_indexes,
        "protectedBaseline": (
            baseline.model_dump(by_alias=True, mode="json") if baseline else None
        ),
        "deltaTemplate": {
            "baselineFingerprint": fingerprint,
            "nodes": [],
            "edges": [],
            "coverage": [],
            "warnings": [],
        },
        "instructions": [
            "Treat protectedBaseline as read-only; never copy it into the delta.",
            "Return only additive nodes, properties, edges, or coverage decisions needed by validationIssues.",
            "Keep baselineFingerprint unchanged.",
            "Use canonicalFactContext factRef values for DUPLICATE_EVIDENCE claims.",
        ],
    }


def _baseline_fingerprint(baseline: GraphPatchFragment | None) -> str:
    payload = (
        baseline.model_dump(by_alias=True, mode="json", exclude_none=True)
        if baseline is not None
        else None
    )
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


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
