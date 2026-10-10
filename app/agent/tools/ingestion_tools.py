"""Các công cụ (ADK Function Tools) phục vụ quy trình nạp và trích xuất tri thức Taekwondo bền vững.

Module này định nghĩa các function tool được xuất bản cho Agent ADK, mỗi công cụ ánh xạ tới
đúng một primitive deterministic trong tầng service operations.
Thứ tự gọi công cụ và luồng điều khiển ngữ nghĩa (semantic flow) được quy định tại SKILL.md.

Danh sách các hàm / phương thức trong module:
- `begin_ingestion(...)`: Khởi tạo mới hoặc tái sử dụng phiên ingestion trong bộ nhớ RAM.
- `get_ingestion_batch(...)`: Lấy các chunk văn bản nguồn và ngữ cảnh canonical của một batch.
- `list_ontology_scopes(...)`: Liệt kê danh mục các scopes nhẹ thuộc phiên bản ontology đang hoạt động.
- `load_ontology_scopes(...)`: Nạp và hợp nhất các snapshot schema ontology tương ứng với danh sách scopes.
- `submit_ingestion_batch(...)`: Gửi kết quả trích xuất fragment đồ thị ngữ nghĩa cho một batch.
- `repair_ingestion_batch(...)`: Sửa đổi, bổ sung thêm tri thức/disposition vào baseline đã được bảo vệ.
- `finalize_ingestion(...)`: Kiểm tra toàn diện các batch và tạo readiness fingerprint chuẩn hóa.
- `fill_ingestion(...)`: Ghi chính thức tri thức đồ thị vào Neo4j và thực hiện đối soát read-back.
- `get_ingestion_status(...)`: Truy vấn trạng thái và tiến độ xử lý hiện tại của tiến trình ingestion.
- `create_schema_proposal(...)`: Tạo và lưu trữ đề xuất mở rộng schema ontology khi phát hiện schema gap.
- `get_schema_proposal(...)`: Lấy thông tin chi tiết và trạng thái của đề xuất schema từ PostgreSQL.
- `review_schema_proposal(...)`: Ghi nhận quyết định phê duyệt hoặc từ chối đề xuất schema từ người dùng.
- `apply_schema_proposal(...)`: Áp dụng đề xuất schema đã duyệt để nâng cấp phiên bản ontology mới.
- `rebase_ingestion(...)`: Chuyển đổi workspace ingestion sang phiên bản ontology mới và cập nhật batch.
- `delete_document(...)`: Vô hiệu hóa tài liệu nguồn và thu hồi tri thức liên quan trong Neo4j và RAM.
- `rollback_document_version(...)`: Quay lui phiên bản tài liệu về phiên bản cũ trước đó trong Neo4j và RAM.
- `get_ingestion_tools(...)`: Trả về danh sách toàn bộ 15 công cụ nạp tri thức cho Agent ADK.
- `_store_checkpoint(...)`: Hàm nội bộ lưu lại checkpoint trạng thái vào tool_context state.
- `_tool_exception(...)`: Hàm nội bộ tạo cấu trúc phản hồi lỗi khi xảy ra Exception.
- `_tool_error(...)`: Hàm nội bộ định dạng phản hồi lỗi nghiệp vụ chuẩn hóa cho tools.
"""

from typing import Any, Literal

from google.adk.tools import ToolContext

from app.core.ingestion_runtime import get_service_container
from app.enums import IngestionJobStatus
from app.schemas import (
    GraphNode,
    GraphPatchFragment,
    SemanticGraphPatchFragment,
    SemanticGraphRepairDelta,
    Workspace,
)
from app.services.ingestion.engine import operations
from app.utils import log_ingestion_event, stable_entity_key

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
    """Khởi tạo mới hoặc tái sử dụng phiên ingestion còn tồn tại trong tiến trình hiện tại (RAM).

    Args:
        artifact_name (str): Tên file artifact tài liệu đính kèm trong session ADK.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.
        document_key (str | None): Khóa định danh tài liệu tùy chọn.
        scope_hint (str | None): Gợi ý phạm vi ontology ban đầu.

    Returns:
        dict[str, Any]: Payload trạng thái workspace (ingestionId, stage, chunks, batches).
    """
    # 1. Đóng gói thông tin yêu cầu đầu vào để ghi log trace
    req = {
        "artifact_name": artifact_name,
        "document_key": document_key,
        "scope_hint": scope_hint,
    }
    try:
        # 3. Tải dữ liệu artifact từ phiên làm việc ADK
        artifact = await tool_context.load_artifact(artifact_name)
        # 3. Kiểm tra tính toàn vẹn của dữ liệu artifact
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

        # 4. Lấy container dịch vụ và thực thi khởi tạo workspace
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

        result_payload = _workspace_payload(result)
        # 5. Lưu lại ingestion_id đang hoạt động vào context state
        if result_payload.get("ingestionId"):
            tool_context.state["active_ingestion_id"] = result_payload["ingestionId"]
            tool_context.state["ingestion_batch_cache"] = {}
            tool_context.state["ingestion_batch_accumulator"] = {
                "ingestionId": result_payload["ingestionId"],
                "batches": {},
                "entities": {},
            }
        # 6. Ghi log sự kiện và trả về kết quả
        log_ingestion_event(f"BEGIN [{artifact_name}]", payload=result_payload, request=req)
        return result_payload
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        # 7. Xử lý ngoại lệ và trả về payload lỗi chuẩn hóa
        result = _tool_exception("artifact_load", None, exc)
        log_ingestion_event(f"BEGIN [{artifact_name}]", payload=result, request=req)
        return result


async def get_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Lấy chunks, source-owned evidenceUnits và ngữ cảnh canonical của một batch.

    Args:
        ingestion_id (str): Mã định danh phiên ingestion.
        batch_index (int): Chỉ số thứ tự của batch cần lấy dữ liệu (0-indexed).
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.

    Returns:
        dict[str, Any]: Payload chứa danh sách chunks và ngữ cảnh thực thể canonicalGraphContext.
    """
    cache = tool_context.state.setdefault("ingestion_batch_cache", {})
    cache_key = f"{ingestion_id}:{batch_index}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    # 1. Đóng gói thông tin yêu cầu để ghi log
    req = {"ingestion_id": ingestion_id, "batch_index": batch_index}
    try:
        # 2. Lấy container và truy vấn nội dung batch
        container = await get_service_container()
        batch, chunks, canonical_nodes = await operations.get_batch(
            container.repository, ingestion_id, batch_index
        )
        batch_dict: dict[str, Any] = {
            "batchIndex": batch.batch_index,
            "scopeHint": (await operations.required_workspace(container.repository, ingestion_id)).job.scope_hint,
            "selectedScopeKeys": batch.scope_keys,
            "chunks": [chunk.model_dump(by_alias=True, mode="json") for chunk in chunks],
            "canonicalGraphContext": [
                {
                    "ref": f"entity:{stable_entity_key(node.class_name, node.identity)}",
                    **node.model_dump(by_alias=True, mode="json"),
                }
                for node in canonical_nodes[:50]
            ],
        }
        accumulator = tool_context.state.get("ingestion_batch_accumulator")
        if (
            isinstance(accumulator, dict)
            and accumulator.get("ingestionId") == ingestion_id
            and isinstance(accumulator.get("entities"), dict)
        ):
            batch_dict["canonicalGraphContext"] = [
                {"ref": ref, **node}
                for ref, node in list(accumulator["entities"].items())[:50]
            ]
        result: dict[str, Any] = {
            "success": True,
            "stage": "batch_retrieved",
            "terminal": False,
            "nextAction": "load_scopes" if batch.scope_keys else "list_scopes",
            "ingestionId": ingestion_id,
            "batch": batch_dict,
        }
        cache[cache_key] = result
        # 3. Ghi log sự kiện và trả về kết quả
        log_ingestion_event(
            f"GET_BATCH [idx={batch_index}]", payload=result, request=req
        )
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        # 4. Xử lý ngoại lệ và trả về lỗi
        result = _tool_exception("batch_retrieval", ingestion_id, exc)
        log_ingestion_event(
            f"GET_BATCH [idx={batch_index}]", payload=result, request=req
        )
        return result


async def list_ontology_scopes(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Liệt kê danh mục scope nhẹ của đúng ontology version đã ghim cho ingestion.

    Args:
        ingestion_id (str): Mã định danh phiên ingestion.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.

    Returns:
        dict[str, Any]: Danh mục các scope có sẵn (scopeKey, displayName, description).
    """
    del tool_context
    # 1. Chuẩn bị request payload ghi log
    req = {"ingestion_id": ingestion_id}
    try:
        # 2. Truy xuất workspace và kiểm tra workflow guard
        container = await get_service_container()
        workspace = await operations.required_workspace(
            container.repository, ingestion_id
        )
        guarded = operations.workflow_guard_payload(workspace, "list_scopes")
        if guarded is not None:
            log_ingestion_event("LIST_SCOPES", payload=guarded, request=req)
            return guarded
        # 3. Liệt kê danh mục scopes từ ontology cache
        result = await operations.list_scopes(
            container.ontology_cache, str(workspace.job.ontology_version_id)
        )
        # 4. Ghi log và trả kết quả
        log_ingestion_event("LIST_SCOPES", payload=result, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        # 5. Xử lý lỗi ngoại lệ
        result = _tool_exception("scope_catalog", ingestion_id, exc)
        log_ingestion_event("LIST_SCOPES", payload=result, request=req)
        return result


async def load_ontology_scopes(
    ingestion_id: str,
    scope_keys: list[str],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Tải và hợp nhất deterministic các compiled snapshots đã chọn cho một batch.

    Args:
        ingestion_id (str): Mã định danh phiên ingestion.
        scope_keys (list[str]): Danh sách khóa scope cần nạp (ví dụ: ['core', 'training']).
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.

    Returns:
        dict[str, Any]: Snapshot schema hợp nhất (entityTypes, properties, relationships).
    """
    # 1. Đóng gói request phục vụ ghi log
    req = {"ingestion_id": ingestion_id, "scope_keys": scope_keys}
    try:
        # 2. Kiểm tra tính hợp lệ của workflow đối với hành động load_scopes
        container = await get_service_container()
        workspace = await operations.required_workspace(
            container.repository, ingestion_id
        )
        guarded = operations.workflow_guard_payload(workspace, "load_scopes")
        if guarded is not None:
            _store_checkpoint(tool_context, guarded)
            log_ingestion_event(
                f"LOAD_SCOPES [{scope_keys}]", payload=guarded, request=req
            )
            return guarded
        # 3. Nạp và hợp nhất projection schema từ cache
        result = await operations.load_scope(
            container.ontology_cache, scope_keys, str(workspace.job.ontology_version_id)
        )
        # 4. Cập nhật state và lưu checkpoint
        tool_context.state["active_ontology_scopes"] = scope_keys
        _store_checkpoint(tool_context, result)
        log_ingestion_event(f"LOAD_SCOPES [{scope_keys}]", payload=result, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        # 5. Xử lý ngoại lệ
        result = _tool_exception("schema_load", ingestion_id, exc)
        _store_checkpoint(tool_context, result)
        log_ingestion_event(f"LOAD_SCOPES [{scope_keys}]", payload=result, request=req)
        return result


async def submit_ingestion_batch(
    ingestion_id: str,
    batch_index: int,
    scope_keys: list[str],
    extraction: SemanticGraphPatchFragment,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Gửi SemanticGraphPatchFragment cho một batch trong một lượt gọi LLM.

    Args:
        ingestion_id (str): Mã định danh phiên ingestion.
        batch_index (int): Chỉ số thứ tự batch đang xử lý (0-indexed).
        scope_keys (list[str]): Danh sách ontology scopes đã chọn cho batch.
        extraction (SemanticGraphPatchFragment): Dữ liệu semantic của batch.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.
        graph_fragment (Any, optional): Tùy chọn fragment legacy tương thích ngược.

    Returns:
        dict[str, Any]: Payload xác nhận staged thành công hoặc danh sách lỗi validation/guards.
    """
    # 1. Serialize the semantic contract only for logging at the adapter seam.
    actual_payload = extraction
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
            "nodes_count": len(payload_dict.get("nodes", [])),
            "edges_count": len(payload_dict.get("edges", [])),
            "coverage_count": len(payload_dict.get("coverage", [])),
        },
    }
    try:
        # 2. Gọi hàm submit_batch trong operations để xác thực và lưu staged
        container = await get_service_container()
        result = await operations.submit_batch(
            container.repository,
            container.ontology_cache,
            ingestion_id,
            batch_index,
            scope_keys,
            actual_payload,
        )

        result = _batch_result_payload(result, batch_index)
        # 3. Lưu trạng thái checkpoint, active_ingestion_id và canonical summary.
        tool_context.state["active_ingestion_id"] = ingestion_id
        if result.get("submittedBatchIndex") == batch_index and result.get("success"):
            workspace = await operations.required_workspace(container.repository, ingestion_id)
            _update_batch_accumulator(
                tool_context,
                ingestion_id,
                batch_index,
                result,
                workspace,
            )
        else:
            workspace = None
        _store_checkpoint(tool_context, result)

        # 4. Thu thập toàn bộ đồ thị tích lũy cho tới thời điểm hiện tại từ các batch đã STAGED
        staged_nodes = []
        staged_edges = []
        staged_batches = []
        if result.get("success") and workspace is not None:
            for b in workspace.batches:
                if b.status == "STAGED" and b.graph_fragment:
                    staged_batches.append(b.batch_index)
                    staged_nodes.extend(b.graph_fragment.get("nodes", []))
                    staged_edges.extend(b.graph_fragment.get("edges", []))

        merged_payload = {
            **result,
            "extraction": payload_dict,
        }
        if staged_batches:
            merged_payload["cumulativeGraph"] = {
                "stagedBatches": sorted(staged_batches),
                "nodes": staged_nodes,
                "edges": staged_edges,
            }

        log_ingestion_event(
            f"SUBMIT_BATCH [idx={batch_index}, scopes={scope_keys}]",
            payload=merged_payload,
            request=req,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        # 6. Bắt và xử lý lỗi nộp batch
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
    """Bổ sung các fact/disposition sửa đổi vào baseline đã bảo vệ mà không thay thế toàn bộ.

    Args:
        ingestion_id (str): Mã định danh phiên ingestion.
        batch_index (int): Chỉ số thứ tự batch cần sửa đổi.
        scope_keys (list[str]): Danh sách scope keys của batch.
        repair_delta (SemanticGraphRepairDelta): Mảnh dữ liệu delta sửa lỗi.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.

    Returns:
        dict[str, Any]: Kết quả sau khi áp dụng repair delta.
    """
    # 1. Trích xuất dữ liệu delta dict
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
        # 2. Thực thi repair_batch trong tầng nghiệp vụ
        container = await get_service_container()
        result = await operations.repair_batch(
            container.repository,
            container.ontology_cache,
            ingestion_id,
            batch_index,
            scope_keys,
            repair_delta,
        )
        result = _batch_result_payload(result, batch_index)
        workspace = None
        if result.get("submittedBatchIndex") == batch_index and result.get("success"):
            workspace = await operations.required_workspace(container.repository, ingestion_id)
            _update_batch_accumulator(
                tool_context,
                ingestion_id,
                batch_index,
                result,
                workspace,
            )
        _store_checkpoint(tool_context, result)

        merged_payload = {**result, "repairDelta": delta_dict}
        if result.get("success") and workspace is not None:
            staged_nodes = []
            staged_edges = []
            staged_batches = []
            for b in workspace.batches:
                if b.status == "STAGED" and b.graph_fragment:
                    staged_batches.append(b.batch_index)
                    staged_nodes.extend(b.graph_fragment.get("nodes", []))
                    staged_edges.extend(b.graph_fragment.get("edges", []))
            if staged_batches:
                merged_payload["cumulativeGraph"] = {
                    "stagedBatches": sorted(staged_batches),
                    "nodes": staged_nodes,
                    "edges": staged_edges,
                }

        log_ingestion_event(
            f"REPAIR_BATCH_DELTA [idx={batch_index}, scopes={scope_keys}]",
            payload=merged_payload,
            request=req,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary returns structured errors
        # 4. Trả về thông báo lỗi ngoại lệ
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
    """Kiểm tra toàn bộ batch và tạo readiness fingerprint deterministic.

    Args:
        ingestion_id (str): Mã định danh phiên ingestion.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.

    Returns:
        dict[str, Any]: Trạng thái hoàn tất sẵn sàng ghi (ready_to_fill) kèm fingerprint.
    """
    # 1. Chuẩn bị request trace
    req = {"ingestion_id": ingestion_id}
    try:
        # 2. Gọi hàm finalize để đối soát độ phủ (coverage integrity)
        container = await get_service_container()
        workspace = await operations.finalize(container.repository, ingestion_id)
        result = _finalize_result_payload(workspace)
        # 3. Lưu checkpoint trạng thái
        _store_checkpoint(tool_context, result)

        # 4. Đóng gói danh sách batches cho MERGE_RESULT phân tích cross-batch
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
        # 5. Xử lý ngoại lệ
        result = _tool_exception("finalize", ingestion_id, exc)
        _store_checkpoint(tool_context, result)
        log_ingestion_event("FINALIZE", payload=result, request=req)
        return result


async def fill_ingestion(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Ghi chính thức tri thức đã hoàn tất nạp vào Neo4j, cập nhật trạng thái COMMITTED và đối soát đọc lại.

    Args:
        ingestion_id (str): Mã định danh phiên ingestion.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.

    Returns:
        dict[str, Any]: Tóm tắt kết quả ghi đồ thị vào Neo4j (nodes, edges, facts, readbackVerified).
    """
    # 1. Chuẩn bị request trace
    req = {"ingestion_id": ingestion_id}
    try:
        # 2. Lấy thông tin workspace và tổng hợp payload chuẩn bị ghi (FILL)
        container = await get_service_container()
        workspace = await operations.required_workspace(
            container.repository, ingestion_id
        )
        fragments = [
            item.graph_fragment for item in workspace.batches if item.graph_fragment
        ]

        fill_prep = {
            "entities_count": sum(len(f.get("nodes", [])) for f in fragments),
            "entities": [
                {
                    "className": n.get("className"),
                    "identity": n.get("identity"),
                    "tempId": n.get("tempId"),
                }
                for f in fragments
                for n in f.get("nodes", [])
            ],
            "facts_count": sum(
                len(n.get("properties", []))
                for f in fragments
                for n in f.get("nodes", [])
            ),
            "relations_count": sum(len(f.get("edges", [])) for f in fragments),
            "relations": [
                {
                    "edgeName": e.get("edgeName"),
                    "sourceTempId": e.get("sourceTempId"),
                    "targetTempId": e.get("targetTempId"),
                    "properties": e.get("properties", {}),
                }
                for f in fragments
                for e in f.get("edges", [])
            ],
            "chunks_count": len(workspace.chunks),
        }

        # 3. Ghi vào Neo4j và đối soát readback
        result = await operations.fill(
            container.repository, container.graph_store, ingestion_id
        )
        # 4. Lưu checkpoint và ghi log chi tiết
        _store_checkpoint(tool_context, result)
        merged_payload = {
            **result,
            "fillPreparation": fill_prep,
        }
        log_ingestion_event("FILL_COMMIT_TO_NEO4J", payload=merged_payload, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        # 5. Bắt lỗi và trả về phản hồi lỗi
        result = _tool_exception("fill", ingestion_id, exc)
        _store_checkpoint(tool_context, result)
        log_ingestion_event("FILL_COMMIT_TO_NEO4J", payload=result, request=req)
        return result


async def get_ingestion_status(
    ingestion_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Truy vấn tiến độ và trạng thái hiện tại của một tiến trình nạp tài liệu.

    Args:
        ingestion_id (str): Mã định danh phiên ingestion.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.

    Returns:
        dict[str, Any]: Payload mô tả trạng thái toàn diện của workspace.
    """
    try:
        # 1. Lấy workspace và trích xuất status payload
        container = await get_service_container()
        workspace = await operations.required_workspace(
            container.repository, ingestion_id
        )
        result = operations.status_payload(workspace)
        # 2. Lưu checkpoint trạng thái
        _store_checkpoint(tool_context, result)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        # 3. Xử lý ngoại lệ
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
        ingestion_id (str): ID phiên ingestion hiện tại.
        batch_index (int): Số thứ tự batch đang xử lý (0-indexed).
        proposal_type (SchemaProposalTypeLiteral): Loại đề xuất thay đổi lược đồ.
        reason (str): Lý do cần thay đổi lược đồ, trích xuất từ nhu cầu tài liệu nguồn.
        affected_scope_keys (list[str]): Danh sách scope bị ảnh hưởng.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.
        technical_name (str | None): Tên kỹ thuật của đối tượng cần tạo hoặc sửa.
        payload (dict[str, Any] | None): Cấu trúc chi tiết của đề xuất.
        evidence (dict[str, Any] | None): Bằng chứng trích xuất từ tài liệu.

    Returns:
        dict[str, Any]: Kết quả tạo đề xuất và trạng thái chờ phê duyệt (awaiting_schema_approval).
    """
    # 1. Chuẩn hóa payload và evidence đầu vào
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
        # 2. Kiểm tra workflow guard đối với thao tác create_schema_proposal
        container = await get_service_container()
        workspace = await operations.required_workspace(
            container.repository, ingestion_id
        )
        guarded = operations.workflow_guard_payload(workspace, "create_schema_proposal")
        if guarded is not None:
            _store_checkpoint(tool_context, guarded)
            log_ingestion_event("CREATE_SCHEMA_PROPOSAL", payload=guarded, request=req)
            return guarded
        # 3. Tạo bản ghi proposal trong PostgreSQL qua ontology_lifecycle
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
        # 4. Chặn batch chuyển sang trạng thái chờ phê duyệt schema (BLOCKED_SCHEMA)
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
        # 5. Lưu ID proposal vào context state và trả kết quả
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
        # 6. Xử lý lỗi
        result = _tool_exception("schema_proposal", ingestion_id, exc)
        _store_checkpoint(tool_context, result)
        log_ingestion_event("CREATE_SCHEMA_PROPOSAL", payload=result, request=req)
        return result


async def get_schema_proposal(
    proposal_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    """Đọc thông tin và trạng thái bền vững của đề xuất lược đồ từ PostgreSQL.

    Args:
        proposal_id (str): Mã định danh đề xuất schema cần tra cứu.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.

    Returns:
        dict[str, Any]: Chi tiết proposal (proposalType, proposalStatus, reason, payload, evidence).
    """
    del tool_context
    try:
        # 1. Truy vấn proposal từ cơ sở dữ liệu PostgreSQL
        container = await get_service_container()
        proposal = await container.ontology_lifecycle.get_proposal(proposal_id)
        # 2. Báo lỗi nếu không tìm thấy proposal
        if proposal is None:
            return _tool_error("schema_proposal", "PROPOSAL_NOT_FOUND", proposal_id)
        # 3. Trả về thông tin chi tiết đề xuất
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
        # 4. Bắt lỗi ngoại lệ
        return _tool_exception("schema_proposal", None, exc)


async def review_schema_proposal(
    proposal_id: str,
    approved: bool,
    reviewed_by: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Ghi nhận quyết định phê duyệt hoặc từ chối đề xuất lược đồ vào PostgreSQL.

    Args:
        proposal_id (str): Mã định danh đề xuất schema.
        approved (bool): Quyết định phê duyệt (True: Đồng ý, False: Từ chối).
        reviewed_by (str): Tên người thực hiện duyệt.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.

    Returns:
        dict[str, Any]: Kết quả cập nhật trạng thái đề xuất (APPROVED hoặc REJECTED).
    """
    # 1. Xác định proposal_id hiệu lực từ tham số hoặc context state
    effective_proposal_id = (
        str(proposal_id).strip()
        if proposal_id and str(proposal_id).strip()
        else tool_context.state.get("pending_schema_proposal_id", "")
    )
    req = {
        "proposal_id": effective_proposal_id,
        "approved": approved,
        "reviewed_by": reviewed_by,
    }
    try:
        # 2. Cập nhật quyết định phê duyệt vào database
        container = await get_service_container()
        proposal = await container.ontology_lifecycle.review_proposal(
            effective_proposal_id, approved=approved, reviewed_by=reviewed_by
        )
        tool_context.state["pending_schema_proposal_id"] = str(proposal.id)
        # 3. Nếu từ chối, cập nhật trạng thái batch trong RAM sang reject
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
        # 4. Trả về kết quả review
        result = {
            "success": True,
            "stage": "schema_proposal_reviewed",
            "proposalId": str(proposal.id),
            "proposalStatus": proposal.status.value,
        }
        log_ingestion_event("REVIEW_SCHEMA_PROPOSAL", payload=result, request=req)
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        # 5. Xử lý ngoại lệ
        result = _tool_exception("schema_proposal_review", None, exc)
        log_ingestion_event("REVIEW_SCHEMA_PROPOSAL", payload=result, request=req)
        return result


async def apply_schema_proposal(
    proposal_id: str,
    new_version_code: str,
    applied_by: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Áp dụng đề xuất đã APPROVED bằng cách biên dịch và kích hoạt phiên bản ontology mới trong PostgreSQL.

    Args:
        proposal_id (str): Mã định danh đề xuất đã được duyệt.
        new_version_code (str): Mã phiên bản ontology mới (ví dụ: 'v3.2.0').
        applied_by (str): Tên người thực hiện áp dụng.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.

    Returns:
        dict[str, Any]: Thông tin phiên bản ontology mới được kích hoạt và hướng dẫn rebase.
    """
    # 1. Xác định proposal_id hiệu lực
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
        # 2. Biên dịch và kích hoạt phiên bản ontology mới trong DB
        container = await get_service_container()
        version = await container.ontology_lifecycle.apply_proposal(
            effective_proposal_id,
            new_version_code=new_version_code,
            applied_by=applied_by,
        )
        # 3. Trả về kết quả apply thành công
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
        # 4. Bắt lỗi ngoại lệ
        result = _tool_exception("schema_proposal_apply", None, exc)
        log_ingestion_event("APPLY_SCHEMA_PROPOSAL", payload=result, request=req)
        return result


async def rebase_ingestion(
    ingestion_id: str,
    target_ontology_version_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Chuyển đổi workspace ingestion trong RAM sang phiên bản ontology mục tiêu từ PostgreSQL.

    Args:
        ingestion_id (str): Mã định danh phiên ingestion.
        target_ontology_version_id (str): Mã UUID của phiên bản ontology mục tiêu.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.

    Returns:
        dict[str, Any]: Trạng thái workspace sau khi rebase.
    """
    # 1. Chuẩn bị request trace
    req = {
        "ingestion_id": ingestion_id,
        "target_ontology_version_id": target_ontology_version_id,
    }
    try:
        # 2. Kiểm tra workflow guard đối với rebase
        container = await get_service_container()
        current = await operations.required_workspace(
            container.repository, ingestion_id
        )
        guarded = operations.workflow_guard_payload(current, "rebase_ingestion")
        if guarded is not None:
            _store_checkpoint(tool_context, guarded)
            log_ingestion_event("REBASE_INGESTION", payload=guarded, request=req)
            return guarded
        # 3. Kiểm tra xem ontology mục tiêu có đang ACTIVE hay không
        active = await container.ontology_cache.active_version()
        if target_ontology_version_id != active.version_id:
            return _tool_error(
                "ontology_rebase",
                "TARGET_ONTOLOGY_NOT_ACTIVE",
                "A job can only be explicitly rebased to the current ACTIVE ontology version.",
            )
        # 4. Lập chỉ mục hash schema của các scopes mục tiêu
        catalog = await container.ontology_cache.list_scopes(target_ontology_version_id)
        hashes = {
            item.scope_key: item.schema_hash for item in catalog if item.schema_hash
        }
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
        # 5. Thực hiện rebase ontology trong repository
        workspace = await container.repository.rebase_ontology_version(
            ingestion_id, target_ontology_version_id, hashes, merged_hashes
        )
        # 6. Invalidated batches are deliberately delegated to the LLM again.
        refreshed_workspace = workspace
        tool_context.state["active_ingestion_id"] = ingestion_id
        tool_context.state["active_ontology_scopes"] = []
        _rebuild_batch_accumulator(tool_context, ingestion_id, refreshed_workspace)
        res = _workspace_payload(refreshed_workspace)
        _store_checkpoint(tool_context, res)
        log_ingestion_event("REBASE_INGESTION", payload=res, request=req)
        return res
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        # 8. Xử lý ngoại lệ
        result = _tool_exception("ontology_rebase", ingestion_id, exc)
        _store_checkpoint(tool_context, result)
        log_ingestion_event("REBASE_INGESTION", payload=result, request=req)
        return result


async def delete_document(
    document_id: str,
    tool_context: ToolContext,
    if_missing: str = "error",
) -> dict[str, Any]:
    """Vô hiệu hóa một tài liệu nguồn và thu hồi các tri thức phụ thuộc trong Knowledge Graph Neo4j và RAM.

    Args:
        document_id (str): Mã định danh tài liệu cần xóa.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.
        if_missing (str, optional): Cách xử lý khi không tìm thấy tài liệu ('error' hoặc 'ignore').

    Returns:
        dict[str, Any]: Kết quả xác nhận xóa tài liệu.
    """
    del tool_context
    # 1. Kiểm tra tính hợp lệ của tham số if_missing
    if if_missing not in {"error", "ignore"}:
        return _tool_error("delete", "INVALID_IF_MISSING", "Use 'error' or 'ignore'")
    req = {"document_id": document_id, "if_missing": if_missing}
    try:
        # 2. Gọi hàm delete_document trong operations để đánh dấu DELETED và deactivate trong Neo4j
        container = await get_service_container()
        result = await operations.delete_document(
            container.repository, container.graph_store, document_id, if_missing
        )
        # 3. Ghi log và trả kết quả
        log_ingestion_event(
            f"DELETE_DOCUMENT [{document_id}]", payload=result, request=req
        )
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        # 4. Xử lý ngoại lệ
        result = _tool_exception("delete", None, exc)
        log_ingestion_event(
            f"DELETE_DOCUMENT [{document_id}]", payload=result, request=req
        )
        return result


async def rollback_document_version(
    document_id: str,
    version_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Thu hồi/quay lui (rollback) phiên bản tài liệu về một phiên bản cũ trước đó trong Knowledge Graph Neo4j và RAM.

    Args:
        document_id (str): Mã định danh tài liệu.
        version_id (str): Mã định danh phiên bản cần quay lui.
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ của Google ADK.

    Returns:
        dict[str, Any]: Kết quả xác nhận rollback phiên bản thành công.
    """
    del tool_context
    # 1. Chuẩn bị request trace
    req = {"document_id": document_id, "version_id": version_id}
    try:
        # 2. Gọi hàm rollback_version trong operations
        container = await get_service_container()
        result = await operations.rollback_version(
            container.repository, container.graph_store, document_id, version_id
        )
        # 3. Ghi log và trả kết quả
        log_ingestion_event(
            f"ROLLBACK_VERSION [{document_id} -> {version_id}]",
            payload=result,
            request=req,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - ADK tool boundary must return structured errors
        # 4. Xử lý lỗi
        result = _tool_exception("rollback", None, exc)
        log_ingestion_event(
            f"ROLLBACK_VERSION [{document_id} -> {version_id}]",
            payload=result,
            request=req,
        )
        return result


# Tuple chứa toàn bộ 15 ADK Ingestion Function Tools phục vụ nạp tri thức
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
)

# Tool surfaces are intentionally split: the root agent orchestrates document-level
# actions while the AgentTool owns all per-batch reads, scope selection and repair.
INGESTION_ROOT_TOOLS = (
    begin_ingestion,
    get_ingestion_status,
    create_schema_proposal,
    get_schema_proposal,
    review_schema_proposal,
    apply_schema_proposal,
    rebase_ingestion,
    finalize_ingestion,
    fill_ingestion,
)

INGESTION_BATCH_TOOLS = (
    get_ingestion_batch,
    list_ontology_scopes,
    load_ontology_scopes,
    submit_ingestion_batch,
    repair_ingestion_batch,
)


def get_ingestion_tools() -> list:
    """Trả về danh sách các tool phục vụ Ingestion để đăng ký vào Agent ADK.

    Returns:
        list: Danh sách các callable tools.
    """
    # 1. Trả về danh sách callable tools từ tuple INGESTION_TOOLS
    return list(INGESTION_TOOLS)


def get_ingestion_root_tools() -> list:
    """Return document-level tools exposed to the root ingestion workflow."""

    return list(INGESTION_ROOT_TOOLS)


def get_ingestion_batch_tools() -> list:
    """Return the narrow per-batch tool surface used by the AgentTool."""

    return list(INGESTION_BATCH_TOOLS)


def _workspace_payload(
    workspace: Workspace,
    *,
    resumed: bool = False,
    idempotent: bool = False,
) -> dict[str, Any]:
    pending = next((item for item in workspace.batches if item.status != "STAGED"), None)
    if workspace.job.status == IngestionJobStatus.COMMITTED:
        stage, terminal, next_action = "committed", True, None
    elif workspace.job.status == IngestionJobStatus.FAILED:
        stage, terminal, next_action = "explicit_extraction_failure", True, "explicit_extraction_failure"
    elif workspace.job.status == IngestionJobStatus.READY:
        stage, terminal, next_action = "ready_to_fill", False, "fill"
    elif pending is None:
        stage, terminal, next_action = "ready_to_finalize", False, "finalize"
    elif pending.status == "BLOCKED_SCHEMA":
        stage, terminal, next_action = "awaiting_schema_approval", False, "wait_for_schema_review"
    elif pending.status == "REPAIR_REQUIRED":
        stage, terminal, next_action = "repair_required", False, "repair_batch"
    else:
        stage, terminal, next_action = "batching", False, "process_batch"
    payload: dict[str, Any] = {
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
        payload["nextBatch"] = {
            "batchIndex": pending.batch_index,
            "chunkIndexes": pending.chunk_indexes,
            "selectedScopeKeys": pending.scope_keys,
        }
    return payload


def _repair_context(batch: Any) -> dict[str, Any] | None:
    if not batch.validated_baseline:
        return None
    baseline = GraphPatchFragment.model_validate(batch.validated_baseline)
    return {
        "protectedBaseline": baseline.model_dump(by_alias=True, mode="json"),
        "baselineFingerprint": operations.baseline_fingerprint(baseline),
        "validationIssues": batch.validation_issues,
        "deltaTemplate": {
            "baselineFingerprint": operations.baseline_fingerprint(baseline),
            "nodes": [],
            "edges": [],
            "coverage": [],
        },
    }


def _batch_result_payload(workspace: Workspace, batch_index: int) -> dict[str, Any]:
    batch = next(item for item in workspace.batches if item.batch_index == batch_index)
    if batch.status == "STAGED":
        payload = _workspace_payload(workspace)
        payload.update(
            {
                "submittedBatchIndex": batch_index,
                "scopeKeys": batch.scope_keys,
                "mergedSchemaHash": batch.merged_schema_hash,
            }
        )
        return payload
    if batch.status == "FAILED":
        return {
            "success": False,
            "stage": "explicit_extraction_failure",
            "terminal": True,
            "retryRequired": False,
            "nextAction": "explicit_extraction_failure",
            "ingestionId": str(workspace.job.id),
            "batchIndex": batch_index,
            "affectedChunkIndexes": batch.chunk_indexes,
            "attempt": batch.validation_attempts,
            "errors": [
                {"code": "BATCH_VALIDATION_RETRY_LIMIT_EXCEEDED", "message": f"Batch {batch_index} exceeded the validation retry limit"},
                *batch.validation_issues,
            ],
        }
    codes = {issue.get("code") for issue in batch.validation_issues}
    schema_gap = bool(codes & {"SCHEMA_GAP_CANDIDATE", "UNKNOWN_ENTITY_TYPE", "UNKNOWN_PROPERTY", "UNKNOWN_RELATIONSHIP"})
    scope_gap = "MISSING_SCOPE" in codes
    payload: dict[str, Any] = {
        "success": False,
        "stage": "schema_gap_candidate" if schema_gap else "scope_reselection_required" if scope_gap else "repair_required",
        "terminal": False,
        "retryRequired": not schema_gap,
        "nextAction": "assess_schema_gap" if schema_gap else "reselect_scopes" if scope_gap else "repair_batch",
        "ingestionId": str(workspace.job.id),
        "batchIndex": batch_index,
        "scopeKeys": batch.scope_keys,
        "affectedChunkIndexes": batch.chunk_indexes,
        "errors": batch.validation_issues,
        "validationAttempts": batch.validation_attempts,
        "maxAttempts": int(operations.MAX_BATCH_VALIDATION_ATTEMPTS),
    }
    context = _repair_context(batch)
    if context is not None:
        payload["repairContext"] = context
    return payload


def _finalize_result_payload(workspace: Workspace) -> dict[str, Any]:
    incomplete = [item.batch_index for item in workspace.batches if item.status != "STAGED"]
    if not incomplete:
        return _workspace_payload(workspace)
    blocked = [item.batch_index for item in workspace.batches if item.status == "BLOCKED_SCHEMA"]
    return {
        "success": False,
        "stage": "awaiting_schema_approval" if blocked else "repair_required",
        "terminal": False,
        "nextAction": "wait_for_schema_review" if blocked else "repair_batches",
        "ingestionId": str(workspace.job.id),
        "repairBatchIndexes": incomplete,
        "errors": [
            {
                "code": "INCOMPLETE_BATCHES",
                "message": f"Batches require schema review: {blocked}" if blocked else f"Batches require repair: {incomplete}",
                "location": "batches",
                "retryable": True,
            }
        ],
    }


def _update_batch_accumulator(
    tool_context: ToolContext,
    ingestion_id: str,
    batch_index: int,
    result: dict[str, Any],
    workspace: Workspace,
) -> None:
    """Persist a compact canonical summary after a batch reaches STAGED.

    The workspace remains the source of truth. Session state only retains the
    lightweight context the next batch needs, not chunks or raw model output.
    """

    if result.get("submittedBatchIndex") != batch_index or not result.get("success"):
        return

    accumulator = tool_context.state.get("ingestion_batch_accumulator")
    if not isinstance(accumulator, dict) or accumulator.get("ingestionId") != ingestion_id:
        accumulator_dict: dict[str, Any] = {"ingestionId": ingestion_id, "batches": {}, "entities": {}}
    else:
        accumulator_dict = dict(accumulator)

    batch = next(item for item in workspace.batches if item.batch_index == batch_index)
    batches_map = accumulator_dict.setdefault("batches", {})
    if isinstance(batches_map, dict):
        batches_map[str(batch_index)] = {
            "status": "STAGED",
            "batchIndex": batch_index,
            "scopeKeys": batch.scope_keys,
            "nextBatch": result.get("nextBatch"),
        }
    entities: dict[str, dict[str, Any]] = {}
    for staged in workspace.batches:
        if staged.status != "STAGED" or not staged.graph_fragment:
            continue
        for raw_node in staged.graph_fragment.get("nodes", []):
            node = GraphNode.model_validate(raw_node)
            ref = f"entity:{stable_entity_key(node.class_name, node.identity)}"
            entities[ref] = node.model_dump(by_alias=True, mode="json")
            if len(entities) >= 50:
                break
        if len(entities) >= 50:
            break
    accumulator_dict["entities"] = entities
    tool_context.state["ingestion_batch_accumulator"] = accumulator_dict


def _rebuild_batch_accumulator(
    tool_context: ToolContext,
    ingestion_id: str,
    workspace: Workspace,
) -> None:
    tool_context.state["ingestion_batch_accumulator"] = {
        "ingestionId": ingestion_id,
        "batches": {},
        "entities": {},
    }
    for batch in workspace.batches:
        if batch.status != "STAGED":
            continue
        _update_batch_accumulator(
            tool_context,
            ingestion_id,
            batch.batch_index,
            {"submittedBatchIndex": batch.batch_index, "success": True},
            workspace,
        )


# Tập hợp các khóa trường thông tin checkpoint cần lưu vào state
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
    """Lưu trữ checkpoint trạng thái xử lý vào tool_context state.

    Args:
        tool_context (ToolContext): Ngữ cảnh thực thi công cụ.
        result (dict[str, Any]): Kết quả phản hồi từ tool.
    """
    # 1. Lọc và lưu các trường thông tin checkpoint không rỗng vào state
    tool_context.state["ingestion_checkpoint"] = {
        key: result.get(key) for key in _CHECKPOINT_KEYS if result.get(key) is not None
    }


def _tool_exception(
    stage: str,
    ingestion_id: str | None,
    exc: Exception,
) -> dict[str, Any]:
    """Hàm phụ trợ định dạng cấu trúc phản hồi lỗi khi xảy ra Exception.

    Args:
        stage (str): Tên giai đoạn xảy ra lỗi.
        ingestion_id (str | None): Mã định danh phiên ingestion (nếu có).
        exc (Exception): Đối tượng Exception gặp phải.

    Returns:
        dict[str, Any]: Payload lỗi chuẩn hóa.
    """
    # 1. Trả về từ điển mô tả lỗi ngoại lệ chuẩn tắc
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
    """Hàm phụ trợ định dạng cấu trúc phản hồi lỗi nghiệp vụ chuẩn tắc cho các tools.

    Args:
        stage (str): Tên giai đoạn xảy ra lỗi.
        code (str): Mã lỗi nghiệp vụ.
        message (str): Thông điệp mô tả chi tiết lỗi.

    Returns:
        dict[str, Any]: Payload lỗi chuẩn hóa.
    """
    # 1. Trả về từ điển mô tả lỗi nghiệp vụ
    return {
        "success": False,
        "stage": stage,
        "terminal": True,
        "ingestionId": None,
        "nextAction": None,
        "errors": [{"code": code, "message": message}],
    }


batch_result_payload = _batch_result_payload
repair_context = _repair_context
store_checkpoint = _store_checkpoint
tool_exception = _tool_exception
update_batch_accumulator = _update_batch_accumulator

__all__ = [
    "INGESTION_TOOLS",
    "batch_result_payload",
    "get_ingestion_tools",
    "repair_context",
    "store_checkpoint",
    "tool_exception",
    "update_batch_accumulator",
]
