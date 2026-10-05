"""Các công cụ (ADK Function Tools) phục vụ quy trình nạp và trích xuất tri thức Taekwondo bền vững.

Mỗi tool ánh xạ tới đúng một primitive deterministic. Quy trình semantic và thứ tự gọi
tool thuộc quyền sở hữu của ingestion SKILL.md.
"""

from typing import Any, Literal

from google.adk.tools import ToolContext

from app.core.ingestion_runtime import get_service_container
from app.schemas import (
    SemanticBatchExtraction,
    SemanticGraphRepairDelta,
)
from app.services.ingestion import operations
from app.utils.ingestion_logger import log_ingestion_event

SchemaProposalTypeLiteral = Literal[
    "NEW_ENTITY_TYPE",
    "NEW_PROPERTY",
    "NEW_RELATIONSHIP",
    "NEW_ALIAS",
    "MODIFY_ENTITY_TYPE",
    "MODIFY_PROPERTY",
    "MODIFY_RELATIONSHIP",
    "NEW_SCOPE",
    "MODIFY_SCOPE",
]


async def begin_ingestion(
    artifact_name: str,
    tool_context: ToolContext,
    document_key: str | None = None,
    scope_hint: str | None = None,
) -> dict[str, Any]:
    """Khởi tạo mới hoặc tái sử dụng phiên ingestion còn tồn tại trong tiến trình hiện tại (RAM)."""
    req = {
        "artifact_name": artifact_name,
        "document_key": document_key,
        "scope_hint": scope_hint,
    }
    try:
        artifact = await tool_context.load_artifact(artifact_name)
        if (
            artifact is None
            or artifact.inline_data is None
            or artifact.inline_data.data is None
        ):
            err = _tool_error(
                "artifact_load",
                "ARTIFACT_NOT_FOUND",
                f"Artifact is unavailable in this ADK session: {artifact_name}",
            )
            log_ingestion_event(f"BEGIN [{artifact_name}]", payload=err, request=req)
            return err

        container = await get_service_container()
        result = await operations.begin(
            container.repository,
            container.ontology_cache,
            artifact_name,
            bytes(artifact.inline_data.data),
            max_file_size=container.max_file_size,
            chunk_size_chars=container.chunk_size_chars,
            batch_size=container.batch_size,
            skill_digest=container.skill_digest,
            model_id=container.model_id,
            document_key=document_key,
            scope_hint=scope_hint,
            mime_type=artifact.inline_data.mime_type,
        )

        if result.get("ingestionId"):
            tool_context.state["active_ingestion_id"] = result["ingestionId"]
        log_ingestion_event(f"BEGIN [{artifact_name}]", payload=result, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("artifact_load", None, exc)
        log_ingestion_event(f"BEGIN [{artifact_name}]", payload=result, request=req)
        return result


async def get_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Lấy chunks, source-owned evidenceUnits và ngữ cảnh canonical của một batch."""
    del tool_context
    req = {"ingestion_id": ingestion_id, "batch_index": batch_index}
    try:
        container = await get_service_container()
        result = await operations.get_batch(
            container.repository, ingestion_id, batch_index
        )
        log_ingestion_event(f"GET_BATCH [idx={batch_index}]", payload=result, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("batch_retrieval", ingestion_id, exc)
        log_ingestion_event(f"GET_BATCH [idx={batch_index}]", payload=result, request=req)
        return result


async def list_ontology_scopes(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Liệt kê danh mục scope nhẹ của đúng ontology version đã ghim cho ingestion."""
    del tool_context
    req = {"ingestion_id": ingestion_id}
    try:
        container = await get_service_container()
        workspace = await operations.required_workspace(container.repository, ingestion_id)
        guarded = operations.workflow_guard_payload(workspace, "list_scopes")
        if guarded is not None:
            log_ingestion_event("LIST_SCOPES", payload=guarded, request=req)
            return guarded
        result = await operations.list_scopes(
            container.ontology_cache, str(workspace.job.ontology_version_id)
        )
        log_ingestion_event("LIST_SCOPES", payload=result, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("scope_catalog", ingestion_id, exc)
        log_ingestion_event("LIST_SCOPES", payload=result, request=req)
        return result


async def load_ontology_scopes(
    ingestion_id: str,
    scope_keys: list[str],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Tải và hợp nhất deterministic các compiled snapshots đã chọn cho một batch."""
    req = {"ingestion_id": ingestion_id, "scope_keys": scope_keys}
    try:
        container = await get_service_container()
        workspace = await operations.required_workspace(container.repository, ingestion_id)
        guarded = operations.workflow_guard_payload(workspace, "load_scopes")
        if guarded is not None:
            _store_checkpoint(tool_context, guarded)
            log_ingestion_event(f"LOAD_SCOPES [{scope_keys}]", payload=guarded, request=req)
            return guarded
        result = await operations.load_scope(
            container.ontology_cache, scope_keys, str(workspace.job.ontology_version_id)
        )
        tool_context.state["active_ontology_scopes"] = scope_keys
        _store_checkpoint(tool_context, result)
        log_ingestion_event(f"LOAD_SCOPES [{scope_keys}]", payload=result, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("schema_load", ingestion_id, exc)
        _store_checkpoint(tool_context, result)
        log_ingestion_event(f"LOAD_SCOPES [{scope_keys}]", payload=result, request=req)
        return result


async def submit_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    scope_keys: list[str],
    extraction: SemanticBatchExtraction,
    tool_context: ToolContext,
    *,
    graph_fragment: Any = None,
) -> dict[str, Any]:
    """Gửi toàn bộ trích xuất Claim Ledger cho một batch trong duy nhất một lượt gọi LLM.

    Args:
        ingestion_id: ID phiên ingestion hiện tại.
        batch_index: Số thứ tự batch cần xử lý (0-indexed).
        scope_keys: Danh sách ontology scopes đã chọn cho batch.
        extraction: Claim ledger hoàn chỉnh; mỗi claim mới phải chọn evidence.evidenceRef từ evidenceUnits của chunk thay vì tự sinh evidence text.
        tool_context: Context ADK tự động inject.
        graph_fragment: Tùy chọn legacy cho fragment cũ nếu có.
    """
    actual_payload = extraction if extraction is not None else graph_fragment
    payload_dict = (
        actual_payload.model_dump(by_alias=True, mode="json")
        if hasattr(actual_payload, "model_dump")
        else (actual_payload if isinstance(actual_payload, dict) else {})
    )
    req = {
        "ingestion_id": ingestion_id,
        "batch_index": batch_index,
        "scope_keys": scope_keys,
        "extraction_summary": {
            "entities_count": len(payload_dict.get("entities", [])),
            "chunks_count": len(payload_dict.get("chunks", [])),
            "claims_count": sum(len(c.get("claims", [])) for c in payload_dict.get("chunks", [])),
        },
    }
    try:
        container = await get_service_container()
        result = await operations.submit_batch(
            container.repository,
            container.ontology_cache,
            ingestion_id,
            batch_index,
            scope_keys,
            actual_payload,
        )

        if result.get("stage") == "schema_review_required" and result.get("schemaGaps"):
            try:
                workspace = await operations.required_workspace(container.repository, ingestion_id)
                enriched_gaps = []
                for gap in result.get("schemaGaps", []):
                    tech_name = gap.get("technicalName")
                    gap_kind = gap.get("kind")
                    prop_type = (
                        "NEW_PROPERTY" if gap_kind == "PROPERTY"
                        else "NEW_RELATIONSHIP" if gap_kind == "RELATIONSHIP"
                        else "NEW_ENTITY_TYPE" if gap_kind == "ENTITY"
                        else "NEW_PROPERTY"
                    )
                    payload_gap = dict(gap)
                    if gap_kind == "PROPERTY" and not payload_gap.get("entityType"):
                        ref = payload_gap.get("entityRef") or payload_gap.get("entity_ref")
                        for ent in (actual_payload.entities if hasattr(actual_payload, "entities") else payload_dict.get("entities", [])):
                            t_id = ent.temp_id if hasattr(ent, "temp_id") else ent.get("tempId") or ent.get("temp_id")
                            c_name = ent.class_name if hasattr(ent, "class_name") else ent.get("className") or ent.get("class_name")
                            if t_id == ref and c_name:
                                payload_gap["entityType"] = c_name
                                break
                    try:
                        proposal = await container.ontology_lifecycle.create_proposal(
                            ingestion_id=ingestion_id,
                            batch_index=batch_index,
                            ontology_version_id=str(workspace.job.ontology_version_id),
                            source_document_id=str(workspace.version.id) if getattr(workspace, "version", None) else None,
                            proposal_type=prop_type,
                            technical_name=tech_name,
                            reason=gap.get("reason") or "Phát hiện schema gap trong quá trình trích xuất tài liệu",
                            payload=payload_gap,
                            evidence=gap.get("evidence") or {},
                            affected_scope_keys=scope_keys,
                        )
                        gap_copy = dict(gap)
                        gap_copy["proposalId"] = str(proposal.id)
                        enriched_gaps.append(gap_copy)
                        tool_context.state["pending_schema_proposal_id"] = str(proposal.id)
                    except Exception:
                        enriched_gaps.append(gap)
                result["schemaGaps"] = enriched_gaps
            except Exception:
                pass

        tool_context.state["active_ingestion_id"] = ingestion_id
        _store_checkpoint(tool_context, result)

        merged_payload = {
            **result,
            "extraction": payload_dict,
        }
        log_ingestion_event(
            f"SUBMIT_BATCH [idx={batch_index}, scopes={scope_keys}]",
            payload=merged_payload,
            request=req,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("batch_submission", ingestion_id, exc)
        _store_checkpoint(tool_context, result)
        log_ingestion_event(
            f"SUBMIT_BATCH [idx={batch_index}]", payload=result, request=req
        )
        return result



async def repair_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    scope_keys: list[str],
    repair_delta: SemanticGraphRepairDelta,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Add facts/dispositions to a protected baseline; never resend or replace it."""

    delta_dict = repair_delta.model_dump(by_alias=True, mode="json")
    req = {
        "ingestion_id": ingestion_id,
        "batch_index": batch_index,
        "scope_keys": scope_keys,
        "delta_summary": {
            "nodes_count": len(delta_dict.get("nodes", [])),
            "edges_count": len(delta_dict.get("edges", [])),
            "coverage_count": len(delta_dict.get("coverage", [])),
        },
    }
    try:
        container = await get_service_container()
        result = await operations.repair_batch(
            container.repository,
            container.ontology_cache,
            ingestion_id,
            batch_index,
            scope_keys,
            repair_delta,
        )
        tool_context.state["active_ingestion_id"] = ingestion_id
        _store_checkpoint(tool_context, result)
        log_ingestion_event(
            f"REPAIR_BATCH_DELTA [idx={batch_index}, scopes={scope_keys}]",
            payload={**result, "repairDelta": delta_dict},
            request=req,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary returns structured errors
        result = _tool_exception("batch_repair", ingestion_id, exc)
        _store_checkpoint(tool_context, result)
        log_ingestion_event(
            f"REPAIR_BATCH_DELTA [idx={batch_index}]", payload=result, request=req
        )
        return result


async def finalize_ingestion(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Kiểm tra toàn bộ batch và tạo readiness fingerprint deterministic."""
    req = {"ingestion_id": ingestion_id}
    try:
        container = await get_service_container()
        workspace = await operations.required_workspace(container.repository, ingestion_id)
        result = await operations.finalize(
            container.repository, container.ontology_cache, ingestion_id
        )
        _store_checkpoint(tool_context, result)

        # Đóng gói danh sách batches cho MERGE_RESULT phân tích cross-batch
        merged_payload = {
            **result,
            "batchesForMerge": [
                {
                    "batch_index": b.batch_index,
                    "graph_fragment": b.graph_fragment,
                }
                for b in workspace.batches
            ],
        }
        log_ingestion_event("FINALIZE", payload=merged_payload, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("finalize", ingestion_id, exc)
        _store_checkpoint(tool_context, result)
        log_ingestion_event("FINALIZE", payload=result, request=req)
        return result


async def fill_ingestion(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Ghi chính thức tri thức đã hoàn tất nạp vào Neo4j, cập nhật trạng thái COMMITTED trong RAM và thực hiện đọc kiểm chứng (read-back)."""
    req = {"ingestion_id": ingestion_id}
    try:
        container = await get_service_container()
        workspace = await operations.required_workspace(container.repository, ingestion_id)
        fragments = [item.graph_fragment for item in workspace.batches if item.graph_fragment]

        # Tóm tắt payload chuẩn bị ghi (FILL)
        fill_prep = {
            "entities_count": sum(len(f.get("nodes", [])) for f in fragments),
            "entities": [
                {"className": n.get("className"), "identity": n.get("identity"), "tempId": n.get("tempId")}
                for f in fragments for n in f.get("nodes", [])
            ],
            "facts_count": sum(len(n.get("properties", [])) for f in fragments for n in f.get("nodes", [])),
            "relations_count": sum(len(f.get("edges", [])) for f in fragments),
            "chunks_count": len(workspace.chunks),
        }

        result = await operations.fill(
            container.repository,
            container.ontology_cache,
            container.graph_store,
            ingestion_id,
        )
        _store_checkpoint(tool_context, result)
        merged_payload = {
            **result,
            "fillPreparation": fill_prep,
        }
        log_ingestion_event("FILL_COMMIT_TO_NEO4J", payload=merged_payload, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("fill", ingestion_id, exc)
        _store_checkpoint(tool_context, result)
        log_ingestion_event("FILL_COMMIT_TO_NEO4J", payload=result, request=req)
        return result


async def get_ingestion_status(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Truy vấn tiến độ và trạng thái hiện tại của một tiến trình nạp tài liệu."""
    try:
        container = await get_service_container()
        workspace = await operations.required_workspace(container.repository, ingestion_id)
        result = operations.status_payload(workspace)
        _store_checkpoint(tool_context, result)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("status", ingestion_id, exc)
        _store_checkpoint(tool_context, result)
        return result


async def create_schema_proposal(
    ingestion_id: str,
    batch_index: int,
    proposal_type: SchemaProposalTypeLiteral,
    reason: str,
    affected_scope_keys: list[str],
    tool_context: ToolContext,
    technical_name: str | None = None,
    payload: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Lưu đề xuất mở rộng/chỉnh sửa lược đồ Ontology Schema khi gặp SCHEMA_GAP_CANDIDATE.

    Args:
        ingestion_id: ID phiên ingestion hiện tại.
        batch_index: Số thứ tự batch đang xử lý (0-indexed).
        proposal_type: Loại đề xuất thay đổi lược đồ. Bắt buộc chọn một trong các giá trị sau:
            - 'NEW_RELATIONSHIP': Thêm quan hệ mới giữa hai thực thể.
            - 'MODIFY_RELATIONSHIP': Chỉnh sửa quan hệ hiện có.
            - 'NEW_ENTITY_TYPE': Thêm loại thực thể mới.
            - 'NEW_PROPERTY': Thêm thuộc tính mới cho một thực thể.
            - 'MODIFY_ENTITY_TYPE': Chỉnh sửa loại thực thể hiện có.
            - 'MODIFY_PROPERTY': Chỉnh sửa thuộc tính hiện có.
            - 'NEW_ALIAS': Thêm bí danh cho thực thể/thuộc tính/quan hệ.
            - 'NEW_SCOPE': Thêm scope mới cho miền tri thức hoàn toàn tách biệt.
            - 'MODIFY_SCOPE': Chỉnh sửa scope hiện có.
        reason: Lý do cần thay đổi lược đồ, trích xuất từ nhu cầu tài liệu nguồn.
        affected_scope_keys: Danh sách scope bị ảnh hưởng (ví dụ: ['core', 'fundamentals', 'training']).
        tool_context: Context ADK tự động inject.
        technical_name: Tên kỹ thuật của đối tượng cần tạo hoặc sửa (ví dụ: 'has_policy').
        payload: Cấu trúc chi tiết của đề xuất (tùy theo proposal_type):
            - Với 'NEW_RELATIONSHIP':
                {"technicalName": "has_policy", "sourceEntityType": "organization", "targetEntityType": "policy", "displayName": "Có chính sách", "cardinality": "MANY_TO_MANY", "description": "..."}
            - Với 'NEW_PROPERTY':
                {"entityType": "class_program", "technicalName": "tuition", "dataType": "FLOAT", "displayName": "Học phí", "required": false}
            - Với 'NEW_ENTITY_TYPE':
                {"technicalName": "event", "displayName": "Sự kiện", "identityFields": ["name"]}
            - Với 'MODIFY_RELATIONSHIP':
                {"technicalName": "has_policy", "description": "...", "cardinality": "MANY_TO_MANY"}
        evidence: Bằng chứng trích xuất từ tài liệu (ví dụ: {"chunkIndex": 20, "quote": "..."}).
    """
    safe_payload = dict(payload) if payload else {}
    safe_evidence = dict(evidence) if evidence else {}
    if technical_name and "technicalName" not in safe_payload:
        safe_payload["technicalName"] = technical_name

    req = {
        "ingestion_id": ingestion_id,
        "batch_index": batch_index,
        "proposal_type": proposal_type,
        "technical_name": technical_name,
        "reason": reason,
        "affected_scope_keys": affected_scope_keys,
        "payload": safe_payload,
        "evidence": safe_evidence,
    }
    try:
        container = await get_service_container()
        workspace = await operations.required_workspace(container.repository, ingestion_id)
        guarded = operations.workflow_guard_payload(workspace, "create_schema_proposal")
        if guarded is not None:
            _store_checkpoint(tool_context, guarded)
            log_ingestion_event("CREATE_SCHEMA_PROPOSAL", payload=guarded, request=req)
            return guarded
        proposal = await container.ontology_lifecycle.create_proposal(
            ingestion_id=ingestion_id,
            batch_index=batch_index,
            ontology_version_id=str(workspace.job.ontology_version_id),
            source_document_id=str(workspace.version.id),
            proposal_type=proposal_type,
            technical_name=technical_name,
            reason=reason,
            payload=safe_payload,
            evidence=safe_evidence,
            affected_scope_keys=affected_scope_keys,
        )
        await container.repository.block_batch_for_proposal(
            ingestion_id,
            batch_index,
            [
                {
                    "code": "SCHEMA_PROPOSAL_PENDING",
                    "message": f"Schema proposal {proposal.id} is waiting for review",
                    "location": f"batch[{batch_index}]",
                    "retryable": True,
                }
            ],
        )
        tool_context.state["pending_schema_proposal_id"] = str(proposal.id)
        result = {
            "success": True,
            "stage": "awaiting_schema_approval",
            "terminal": False,
            "retryRequired": False,
            "nextAction": "wait_for_user_review",
            "ingestionId": ingestion_id,
            "batchIndex": batch_index,
            "proposalId": str(proposal.id),
            "proposalStatus": proposal.status.value,
        }
        log_ingestion_event("CREATE_SCHEMA_PROPOSAL", payload=result, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("schema_proposal", ingestion_id, exc)
        _store_checkpoint(tool_context, result)
        log_ingestion_event("CREATE_SCHEMA_PROPOSAL", payload=result, request=req)
        return result


async def get_schema_proposal(
    proposal_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    """??c th?ng tin v? tr?ng th?i b?n v?ng c?a ?? xu?t l??c ?? t? PostgreSQL."""
    del tool_context
    try:
        container = await get_service_container()
        proposal = await container.ontology_lifecycle.get_proposal(proposal_id)
        if proposal is None:
            return _tool_error("schema_proposal", "PROPOSAL_NOT_FOUND", proposal_id)
        return {
            "success": True,
            "stage": "schema_proposal",
            "proposalId": str(proposal.id),
            "proposalType": proposal.proposal_type.value,
            "proposalStatus": proposal.status.value,
            "reason": proposal.reason,
            "payload": proposal.payload,
            "evidence": proposal.evidence,
            "affectedScopeKeys": proposal.affected_scope_keys,
            "appliedOntologyVersionId": str(proposal.applied_ontology_version_id)
            if proposal.applied_ontology_version_id
            else None,
        }
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        return _tool_exception("schema_proposal", None, exc)


async def review_schema_proposal(
    proposal_id: str,
    approved: bool,
    reviewed_by: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Ghi nhận quyết định phê duyệt hoặc từ chối đề xuất lược đồ vào PostgreSQL; không bao giờ tự gọi nếu không có hướng dẫn từ người dùng."""
    effective_proposal_id = (
        str(proposal_id).strip()
        if proposal_id and str(proposal_id).strip()
        else tool_context.state.get("pending_schema_proposal_id", "")
    )
    req = {"proposal_id": effective_proposal_id, "approved": approved, "reviewed_by": reviewed_by}
    try:
        container = await get_service_container()
        proposal = await container.ontology_lifecycle.review_proposal(
            effective_proposal_id, approved=approved, reviewed_by=reviewed_by
        )
        tool_context.state["pending_schema_proposal_id"] = str(proposal.id)
        if (
            not approved
            and proposal.source_ingestion_id
            and proposal.source_batch_index is not None
        ):
            try:
                await container.repository.reject_batch_schema_proposal(
                    proposal.source_ingestion_id,
                    proposal.source_batch_index,
                    [
                        {
                            "code": "SCHEMA_PROPOSAL_REJECTED",
                            "message": f"Schema proposal {proposal.id} was rejected",
                            "location": f"batch[{proposal.source_batch_index}]",
                            "retryable": True,
                        }
                    ],
                )
            except KeyError:
                pass
        result = {
            "success": True,
            "stage": "schema_proposal_reviewed",
            "proposalId": str(proposal.id),
            "proposalStatus": proposal.status.value,
        }
        log_ingestion_event("REVIEW_SCHEMA_PROPOSAL", payload=result, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("schema_proposal_review", None, exc)
        log_ingestion_event("REVIEW_SCHEMA_PROPOSAL", payload=result, request=req)
        return result


async def apply_schema_proposal(
    proposal_id: str,
    new_version_code: str,
    applied_by: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Áp dụng đề xuất đã APPROVED bằng cách biên dịch và kích hoạt phiên bản ontology mới trong PostgreSQL."""
    effective_proposal_id = (
        str(proposal_id).strip()
        if proposal_id and str(proposal_id).strip()
        else tool_context.state.get("pending_schema_proposal_id", "")
    )
    req = {
        "proposal_id": effective_proposal_id,
        "new_version_code": new_version_code,
        "applied_by": applied_by,
    }
    try:
        container = await get_service_container()
        version = await container.ontology_lifecycle.apply_proposal(
            effective_proposal_id, new_version_code=new_version_code, applied_by=applied_by
        )
        result = {
            "success": True,
            "stage": "schema_proposal_applied",
            "proposalId": effective_proposal_id,
            "ontologyVersionId": str(version.id),
            "ontologyVersion": version.version,
            "nextAction": "rebase_ingestion",
        }
        log_ingestion_event("APPLY_SCHEMA_PROPOSAL", payload=result, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("schema_proposal_apply", None, exc)
        log_ingestion_event("APPLY_SCHEMA_PROPOSAL", payload=result, request=req)
        return result


async def rebase_ingestion(
    ingestion_id: str,
    target_ontology_version_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Chuyển đổi workspace ingestion trong RAM sang phiên bản ontology mục tiêu từ PostgreSQL và vô hiệu hóa các batch bị ảnh hưởng."""
    req = {
        "ingestion_id": ingestion_id,
        "target_ontology_version_id": target_ontology_version_id,
    }
    try:
        container = await get_service_container()
        current = await operations.required_workspace(container.repository, ingestion_id)
        guarded = operations.workflow_guard_payload(current, "rebase_ingestion")
        if guarded is not None:
            _store_checkpoint(tool_context, guarded)
            log_ingestion_event("REBASE_INGESTION", payload=guarded, request=req)
            return guarded
        active = await container.ontology_cache.active_version()
        if target_ontology_version_id != active.version_id:
            return _tool_error(
                "ontology_rebase",
                "TARGET_ONTOLOGY_NOT_ACTIVE",
                "A job can only be explicitly rebased to the current ACTIVE ontology version.",
            )
        catalog = await container.ontology_cache.list_scopes(target_ontology_version_id)
        hashes = {item.scope_key: item.schema_hash for item in catalog if item.schema_hash}
        merged_hashes: dict[str, str] = {}
        for batch in current.batches:
            if batch.scope_keys and all(
                hashes.get(key) == batch.snapshot_hashes.get(key)
                for key in batch.scope_keys
            ):
                projection = await container.ontology_cache.get_many(
                    batch.scope_keys, target_ontology_version_id
                )
                merged_hashes["\x1f".join(batch.scope_keys)] = projection.digest
        workspace = await container.repository.rebase_ontology_version(
            ingestion_id, target_ontology_version_id, hashes, merged_hashes
        )
        for batch in workspace.batches:
            if batch.claim_ledger and batch.status != "STAGED":
                try:
                    await operations.recompile_batch_from_ledger(
                        container.repository,
                        container.ontology_cache,
                        ingestion_id,
                        batch.batch_index,
                    )
                except Exception as exc:  # noqa: BLE001
                    log_ingestion_event(
                        f"REBASE_AUTO_RECOMPILE_FAILED [idx={batch.batch_index}]",
                        payload={"error": str(exc)},
                        request=req,
                    )
        refreshed_workspace = await operations.required_workspace(
            container.repository, ingestion_id
        )
        tool_context.state["active_ingestion_id"] = ingestion_id
        res = operations.status_payload(refreshed_workspace)
        _store_checkpoint(tool_context, res)
        log_ingestion_event("REBASE_INGESTION", payload=res, request=req)
        return res
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("ontology_rebase", ingestion_id, exc)
        _store_checkpoint(tool_context, result)
        log_ingestion_event("REBASE_INGESTION", payload=result, request=req)
        return result


async def delete_document(
    document_id: str,
    tool_context: ToolContext,
    if_missing: str = "error",
) -> dict[str, Any]:
    """Vô hiệu hóa một tài liệu nguồn và thu hồi các tri thức phụ thuộc trong Knowledge Graph Neo4j cũng như cập nhật trạng thái trong RAM."""
    del tool_context
    if if_missing not in {"error", "ignore"}:
        return _tool_error("delete", "INVALID_IF_MISSING", "Use 'error' or 'ignore'")
    req = {"document_id": document_id, "if_missing": if_missing}
    try:
        container = await get_service_container()
        result = await operations.delete_document(
            container.repository, container.graph_store, document_id, if_missing
        )
        log_ingestion_event(f"DELETE_DOCUMENT [{document_id}]", payload=result, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("delete", None, exc)
        log_ingestion_event(f"DELETE_DOCUMENT [{document_id}]", payload=result, request=req)
        return result


async def rollback_document_version(
    document_id: str,
    version_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Thu hồi/quay lui (rollback) phiên bản tài liệu về một phiên bản cũ trước đó trong Knowledge Graph Neo4j và cập nhật trạng thái trong RAM."""
    del tool_context
    req = {"document_id": document_id, "version_id": version_id}
    try:
        container = await get_service_container()
        result = await operations.rollback_version(
            container.repository, container.graph_store, document_id, version_id
        )
        log_ingestion_event(
            f"ROLLBACK_VERSION [{document_id} -> {version_id}]", payload=result, request=req
        )
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        result = _tool_exception("rollback", None, exc)
        log_ingestion_event(
            f"ROLLBACK_VERSION [{document_id} -> {version_id}]", payload=result, request=req
        )
        return result


# Danh sách toàn bộ các ADK Ingestion Tools được xuất bản cho Agent
INGESTION_TOOLS = (
    begin_ingestion,
    get_ingestion_batch,
    submit_ingestion_batch,
    finalize_ingestion,
    fill_ingestion,
    get_ingestion_status,
    list_ontology_scopes,
    load_ontology_scopes,
    create_schema_proposal,
    get_schema_proposal,
    review_schema_proposal,
    apply_schema_proposal,
    rebase_ingestion,
    delete_document,
    rollback_document_version,
)



def get_ingestion_tools() -> list:
    """Trả về danh sách các tool phục vụ Ingestion để đăng ký vào Agent ADK."""
    return list(INGESTION_TOOLS)


_CHECKPOINT_KEYS = (
    "success",
    "stage",
    "terminal",
    "retryRequired",
    "nextAction",
    "ingestionId",
    "processedBatches",
    "remainingBatches",
    "nextBatch",
    "batchIndex",
    "scopeKeys",
    "affectedChunkIndexes",
    "errors",
    "validationAttempts",
    "attempt",
    "maxAttempts",
    "repairBatchIndexes",
)


def _store_checkpoint(tool_context: ToolContext, result: dict[str, Any]) -> None:
    tool_context.state["ingestion_checkpoint"] = {
        key: result.get(key)
        for key in _CHECKPOINT_KEYS
        if result.get(key) is not None
    }


def _tool_exception(
    stage: str,
    ingestion_id: str | None,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "success": False,
        "stage": stage,
        "terminal": True,
        "retryRequired": False,
        "ingestionId": ingestion_id,
        "nextAction": "report_tool_failure",
        "errors": [
            {
                "code": "TOOL_EXECUTION_ERROR",
                "message": str(exc),
                "retryable": False,
            }
        ],
    }


def _tool_error(stage: str, code: str, message: str) -> dict[str, Any]:
    """Hàm phụ trợ định dạng cấu trúc phản hồi lỗi chuẩn tắc cho các tools."""
    return {
        "success": False,
        "stage": stage,
        "terminal": True,
        "ingestionId": None,
        "nextAction": None,
        "errors": [{"code": code, "message": message}],
    }


__all__ = ["INGESTION_TOOLS", "get_ingestion_tools"]
