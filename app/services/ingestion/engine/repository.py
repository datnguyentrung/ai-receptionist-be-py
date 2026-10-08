"""Process-local Ingestion Repository & Workspace Store.

Module này cung cấp lớp lưu trữ trạng thái phiên làm việc (Workspace Store) trong bộ nhớ RAM
cho toàn bộ quy trình nạp tài liệu phân tầng (Staged Ingestion Pipeline).
PostgreSQL không được dùng để lưu trạng thái trung gian của job ingestion nhằm tối ưu tốc độ;
thay vào đó, RAM lưu trữ các workspace và Neo4j lưu trữ Knowledge Graph hoàn chỉnh sau khi commit.

Danh sách các nhóm phương thức trong module:
1. KHỞI TẠO & TRUY VẤN WORKSPACE (Workspace Lifecycle & Query):
    - `__init__`: Khởi tạo kho lưu trữ in-memory.
    - `create_or_resume`: Tạo mới hoặc tái sử dụng workspace còn hiệu lực trong RAM.
    - `get_workspace`: Lấy thông tin workspace theo ingestion_id.
    - `list_committed_workspaces`: Danh sách các workspace đã commit thành công.

2. XỬ LÝ KẾT QUẢ & KIỂM SOÁT LỖI BATCH (Batch Execution & Validation):
    - `store_batch_result`: Lưu kết quả trích xuất và validation của một batch (STAGED/REPAIR_REQUIRED/FAILED).
    - `mark_batch_for_repair`: Mở lại batch để yêu cầu trích xuất/sửa chữa lại do lỗi toàn cục.
    - `block_batch_for_proposal`: Chặn batch chờ phê duyệt mở rộng ontology schema (BLOCKED_SCHEMA).
    - `reject_batch_schema_proposal`: Đánh dấu đề xuất mở rộng schema của batch bị từ chối (SCHEMA_REJECTED).

3. CHUYỂN ĐỔI TRẠNG THÁI JOB & HOÀN TẤT (Job State Transitions & Commit):
    - `mark_ready`: Đánh dấu toàn bộ workspace đã sẵn sàng để ghi vào database (READY).
    - `mark_writing`: Chuyển trạng thái workspace sang đang ghi dữ liệu (WRITING).
    - `mark_committed`: Hoàn tất nạp dữ liệu và đánh dấu COMMITTED.
    - `mark_failed`: Đánh dấu phiên ingestion thất bại kèm nguyên nhân (FAILED).

4. QUẢN LÝ PHIÊN BẢN & REBASE ONTOLOGY (Document Versioning & Ontology Rebase):
    - `set_document_version_status`: Cập nhật trạng thái phiên bản tài liệu (DELETED, ROLLED_BACK,...).
    - `rebase_ontology_version`: Chuyển đổi workspace sang phiên bản ontology mới.

5. HÀM TRỢ GIÚP CẬP NHẬT DỮ LIỆU NỘI BỘ (Internal Mutation Helpers):
    - `_required`: Lấy workspace bắt buộc hoặc ném lỗi KeyError.
    - `_workspace_and_batch`: Lấy cặp workspace và batch theo index.
    - `_replace_batch`: Cập nhật thông tin batch trong workspace bất biến.
    - `_replace_version_status`: Cập nhật trạng thái version trong workspace.

Các hàm helper tiện ích đã được tách sang module ngoài: `app.utils.ingestion_helpers`.
"""

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from app.enums import IngestionJobStatus, SourceVersionStatus
from app.schemas import (
    ActiveOntology,
    DocumentChunk,
    GraphPatchFragment,
    IngestionBatchData,
    IngestionChunkData,
    IngestionDocumentData,
    IngestionDocumentVersionData,
    IngestionJobData,
    SemanticGraphPatchFragment,
    Workspace,
)
from app.services.ingestion.pipeline.staged_ingestion import IngestionWorkspaceService
from app.utils.ingestion_helpers import (
    dict_digest,
    to_uuid,
)

MAPPER_VERSION = "taekwondo-mapper-v2"


# ==============================================================================
# CLASS INGESTION REPOSITORY
# ==============================================================================

class IngestionRepository:
    """Lớp quản lý lưu trữ workspace nạp tài liệu trong bộ nhớ RAM (Process-local Workspace Store).

    PostgreSQL không được sử dụng để lưu trạng thái job ingestion tạm thời.
    PostgreSQL chỉ lưu trữ các thực thể bền vững (ontology versions, scopes, compiled snapshots, proposals).
    Dữ liệu đồ thị tri thức trích xuất hoàn chỉnh sẽ được lưu trực tiếp vào Neo4j.
    """

    # ==========================================================================
    # NHÓM 1: KHỞI TẠO & TRUY VẤN WORKSPACE (Workspace Lifecycle & Query)
    # ==========================================================================

    # --------------------------------------------------------------------------
    # Khởi tạo kho lưu trữ in-memory cho các workspace ingestion.
    # Đầu vào: Không có tham số bắt buộc.
    # Đầu ra: Đối tượng IngestionRepository.
    # --------------------------------------------------------------------------
    def __init__(self, *_: Any, **__: Any) -> None:
        self._workspaces: dict[str, Workspace] = {}
        self._by_signature: dict[str, str] = {}
        self._document_current_version: dict[str, uuid.UUID] = {}

    # --------------------------------------------------------------------------
    # Tạo mới hoặc tái sử dụng workspace ingestion còn tồn tại trong tiến trình hiện tại.
    #
    # Tác dụng:
    #   Tính toán chữ ký ingestion dựa trên nội dung tài liệu, cấu hình batch, phiên bản ontology,
    #   prompt skill digest và mô hình LLM. Nếu chữ ký đã tồn tại trong RAM, workspace sẽ được tái
    #   sử dụng nhằm tránh lãng phí chi phí gọi LLM trích xuất lại từ đầu.
    #
    # Đầu vào (Input):
    #   - artifact_name (str): Tên tệp hoặc URI định danh của tài liệu nguồn.
    #   - content_hash (str): Mã băm SHA-256 nội dung của toàn bộ tài liệu nguồn.
    #   - chunks (list[DocumentChunk]): Danh sách các đoạn văn bản (chunks) đã tiền xử lý.
    #   - ontology (ActiveOntology): Thông tin phiên bản ontology đang hoạt động.
    #   - document_key (str): Khóa định danh duy nhất của tài liệu trong hệ thống.
    #   - scope_hint (str | None): Gợi ý phạm vi chuyên môn (scope key) nếu có.
    #   - skill_digest (str): Mã băm của prompt/skill trích xuất tri thức.
    #   - model_id (str): Định danh mô hình LLM thực hiện trích xuất.
    #   - compiler_version (str): Phiên bản của trình biên dịch ontology.
    #   - batch_size (int): Số lượng chunk tối đa trong 1 batch.
    #   - max_batch_chars (int): Số ký tự tối đa cho phép trong 1 batch.
    #
    # Đầu ra (Output):
    #   - tuple[Workspace, bool, bool]: Gồm (workspace, is_resumed, is_already_committed).
    # --------------------------------------------------------------------------
    async def create_or_resume(
        self,
        *,
        artifact_name: str,
        content_hash: str,
        chunks: list[DocumentChunk],
        ontology: ActiveOntology,
        document_key: str,
        scope_hint: str | None,
        skill_digest: str,
        model_id: str,
        compiler_version: str,
        batch_size: int,
        max_batch_chars: int,
    ) -> tuple[Workspace, bool, bool]:
        config_signature = dict_digest(
            {
                "maxBatchChunks": batch_size,
                "maxBatchChars": max_batch_chars,
            }
        )
        ingestion_signature = dict_digest(
            {
                "contentHash": content_hash,
                "configSignature": config_signature,
                "ontologyVersionId": ontology.version_id,
                "skillDigest": skill_digest,
                "modelId": model_id,
                "mapperVersion": MAPPER_VERSION,
                "compilerVersion": compiler_version,
            }
        )
        existing_id = self._by_signature.get(ingestion_signature)
        if existing_id:
            workspace = self._workspaces[existing_id]
            committed = workspace.version.status == SourceVersionStatus.COMMITTED
            return workspace, not committed, committed

        document_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"ingestion-document:{document_key}"
        )
        version_id = uuid.uuid4()
        job_id = uuid.uuid4()
        ontology_id = to_uuid(ontology.version_id)
        document = IngestionDocumentData(
            id=document_id,
            document_key=document_key,
            name=artifact_name,
            current_version_id=self._document_current_version.get(document_key),
        )
        version = IngestionDocumentVersionData(
            id=version_id,
            document_id=document_id,
            content_hash=content_hash,
            ontology_version_id=ontology_id,
            ontology_digest=dict_digest(
                {"versionId": ontology.version_id, "version": ontology.version}
            ),
            status=SourceVersionStatus.PENDING,
        )
        job = IngestionJobData(
            id=job_id,
            document_version_id=version_id,
            ontology_version_id=ontology_id,
            status=IngestionJobStatus.BATCHING,
            stage="batching",
            scope_hint=scope_hint,
            readiness_fingerprint=None,
            error_stage=None,
            error_message=None,
            summary={},
        )
        ingestion_chunks = tuple(
            IngestionChunkData(
                id=uuid.uuid4(),
                document_version_id=version_id,
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.index,
                text=chunk.content,
                content_hash=chunk.content_hash,
                token_count=max(1, (len(chunk.content) + 3) // 4),
                section=chunk.section,
                page_start=None,
                page_end=None,
                source_anchor=f"{chunk.source}#{chunk.structural_path}:L{chunk.start_line}-L{chunk.end_line}",
            )
            for chunk in chunks
        )
        partitioned = IngestionWorkspaceService.partition(
            chunks,
            max_batch_chunks=batch_size,
            max_batch_chars=max_batch_chars,
        )
        batches = tuple(
            IngestionBatchData(
                id=uuid.uuid4(),
                job_id=job_id,
                batch_index=batch.index,
                chunk_indexes=batch.chunk_indexes,
                status="PENDING",
                semantic_fragment=None,
                graph_fragment=None,
                validation_issues=[],
                validation_attempts=0,
                merged_schema_hash=None,
            )
            for batch in partitioned
        )
        workspace = Workspace(
            document=document,
            version=version,
            job=job,
            chunks=ingestion_chunks,
            batches=batches,
        )
        self._workspaces[str(job_id)] = workspace
        self._by_signature[ingestion_signature] = str(job_id)
        return workspace, False, False

    # --------------------------------------------------------------------------
    # Lấy thông tin workspace ingestion theo định danh ID.
    #
    # Đầu vào (Input):
    #   - ingestion_id (str): Mã UUID của job/workspace cần tìm.
    #
    # Đầu ra (Output):
    #   - Workspace | None: Đối tượng Workspace nếu tìm thấy, ngược lại trả về None.
    # --------------------------------------------------------------------------
    async def get_workspace(self, ingestion_id: str) -> Workspace | None:
        return self._workspaces.get(str(ingestion_id))

    # --------------------------------------------------------------------------
    # Liệt kê danh sách các workspace đã hoàn tất trích xuất và được commit thành công.
    #
    # Đầu vào (Input):
    #   - document_id (str | None): ID tài liệu cần lọc (nếu None sẽ lấy tất cả).
    #   - limit (int): Số lượng workspace tối đa trả về (mặc định 1000).
    #
    # Đầu ra (Output):
    #   - list[Workspace]: Danh sách các workspace thỏa mãn điều kiện trạng thái COMMITTED.
    # --------------------------------------------------------------------------
    async def list_committed_workspaces(
        self, document_id: str | None = None, limit: int = 1000
    ) -> list[Workspace]:
        workspaces = [
            item
            for item in self._workspaces.values()
            if item.job.status == IngestionJobStatus.COMMITTED
            and (document_id is None or str(item.document.id) == document_id)
        ]
        return workspaces[:limit]

    # ==========================================================================
    # NHÓM 2: XỬ LÝ KẾT QUẢ & KIỂM SOÁT LỖI BATCH (Batch Execution & Validation)
    # ==========================================================================

    # --------------------------------------------------------------------------
    # Lưu kết quả trích xuất và đối soát hợp lệ của một batch cụ thể vào workspace.
    #
    # Tác dụng:
    #   Hàm cập nhật thông tin fragment, tăng số lần thử validation và xác định trạng thái mới của batch:
    #   - STAGED: Nếu không có lỗi validation.
    #   - REPAIR_REQUIRED: Nếu có lỗi nhưng chưa vượt quá số lần thử tối đa.
    #   - FAILED: Nếu số lần thử validation vượt quá max_attempts.
    #
    # Đầu vào (Input):
    #   - ingestion_id (str): Mã định danh phiên ingestion.
    #   - batch_index (int): Thứ tự của batch trong workspace.
    #   - scope_bindings (list[dict[str, str]]): Danh sách scope và schema hash ràng buộc.
    #   - merged_schema_hash (str | None): Mã hash của schema sau khi gộp scopes.
    #   - semantic_fragment (SemanticGraphPatchFragment | dict | None): Mảnh đồ thị ngữ nghĩa thô từ LLM.
    #   - fragment (GraphPatchFragment | dict | None): Mảnh đồ thị đã resolved identity.
    #   - issues (list[dict]): Danh sách các lỗi/cảnh báo phát hiện trong quá trình validation.
    #   - max_attempts (int): Số lần thử nghiệm tối đa cho phép sửa lỗi batch.
    #   - validated_baseline (GraphPatchFragment | dict | None): Bản baseline đã validate trước đó.
    #
    # Đầu ra (Output):
    #   - Workspace: Đối tượng Workspace sau khi đã cập nhật batch và trạng thái job.
    #
    # Lỗi (Raises):
    #   - RuntimeError: Nếu job không ở trạng thái BATCHING hoặc batch bị khóa bởi đề xuất schema.
    # --------------------------------------------------------------------------
    async def store_batch_result(
        self,
        ingestion_id: str,
        batch_index: int,
        scope_bindings: list[dict[str, str]],
        merged_schema_hash: str | None,
        semantic_fragment: SemanticGraphPatchFragment | dict | None,
        fragment: GraphPatchFragment | dict | None,
        issues: list[dict],
        *,
        max_attempts: int,
        validated_baseline: GraphPatchFragment | dict | None = None,
    ) -> Workspace:
        workspace, batch = self._workspace_and_batch(ingestion_id, batch_index)
        if workspace.job.status != IngestionJobStatus.BATCHING:
            raise RuntimeError(
                f"Cannot submit a batch while ingestion is {workspace.job.status}"
            )
        if batch.status in {"BLOCKED_SCHEMA", "SCHEMA_REJECTED"}:
            raise RuntimeError(
                f"Batch {batch_index} is gated by schema review ({batch.status})"
            )
        attempts = batch.validation_attempts + 1
        terminal = bool(issues) and attempts > max_attempts

        if not issues and fragment is not None:
            baseline_dict = (
                getattr(fragment, "model_dump")(by_alias=True, mode="json")
                if hasattr(fragment, "model_dump")
                else fragment
            )
        elif validated_baseline is not None:
            baseline_dict = (
                getattr(validated_baseline, "model_dump")(by_alias=True, mode="json")
                if hasattr(validated_baseline, "model_dump")
                else validated_baseline
            )
        else:
            baseline_dict = batch.validated_baseline

        updated = replace(
            batch,
            validation_attempts=attempts,
            validation_issues=issues,
            merged_schema_hash=merged_schema_hash,
            semantic_fragment=getattr(semantic_fragment, "model_dump")(by_alias=True, mode="json")
            if hasattr(semantic_fragment, "model_dump")
            else semantic_fragment,
            graph_fragment=getattr(fragment, "model_dump")(by_alias=True, mode="json")
            if hasattr(fragment, "model_dump")
            else fragment,
            validated_baseline=baseline_dict,
            status="FAILED" if terminal else "REPAIR_REQUIRED" if issues else "STAGED",
            scope_keys=[item["scopeKey"] for item in scope_bindings],
            snapshot_hashes={
                item["scopeKey"]: item["schemaHash"] for item in scope_bindings
            },
        )
        workspace.job.status = (
            IngestionJobStatus.FAILED if terminal else IngestionJobStatus.BATCHING
        )
        workspace.job.stage = "explicit_extraction_failure" if terminal else "batching"
        if terminal:
            workspace.job.error_stage = "explicit_extraction_failure"
            workspace.job.error_message = (
                f"Batch {batch_index} exceeded the validation retry limit"
            )
            workspace = self._replace_version_status(
                workspace, SourceVersionStatus.FAILED
            )
        workspace.job.readiness_fingerprint = None
        return self._replace_batch(workspace, updated)

    # --------------------------------------------------------------------------
    # Mở lại trạng thái REPAIR_REQUIRED cho một batch khi phát hiện vi phạm ràng buộc toàn cục.
    #
    # Tác dụng:
    #   Dùng khi kiểm tra tổng thể ở bước hoàn tất phát hiện bất thường cần trích xuất lại.
    #
    # Đầu vào (Input):
    #   - ingestion_id (str): Mã định danh phiên ingestion.
    #   - batch_index (int): Chỉ số index của batch cần sửa.
    #   - issues (list[dict]): Danh sách các lỗi vi phạm cần khắc phục.
    #
    # Đầu ra (Output):
    #   - Workspace: Workspace đã cập nhật trạng thái batch sang REPAIR_REQUIRED.
    # --------------------------------------------------------------------------
    async def mark_batch_for_repair(
        self, ingestion_id: str, batch_index: int, issues: list[dict]
    ) -> Workspace:
        workspace, batch = self._workspace_and_batch(ingestion_id, batch_index)
        updated = replace(
            batch,
            status="REPAIR_REQUIRED",
            validation_issues=issues,
        )
        workspace.job.status = IngestionJobStatus.BATCHING
        workspace.job.stage = "batching"
        workspace.job.readiness_fingerprint = None
        return self._replace_batch(workspace, updated)

    # --------------------------------------------------------------------------
    # Chặn batch và chuyển trạng thái sang BLOCKED_SCHEMA chờ phê duyệt mở rộng ontology.
    #
    # Đầu vào (Input):
    #   - ingestion_id (str): Mã định danh phiên ingestion.
    #   - batch_index (int): Chỉ số index của batch.
    #   - issues (list[dict]): Danh sách các vấn đề schema gap cần đề xuất.
    #
    # Đầu ra (Output):
    #   - Workspace: Workspace ở trạng thái chờ duyệt schema (BLOCKED_SCHEMA).
    # --------------------------------------------------------------------------
    async def block_batch_for_proposal(
        self, ingestion_id: str, batch_index: int, issues: list[dict]
    ) -> Workspace:
        workspace, batch = self._workspace_and_batch(ingestion_id, batch_index)
        updated = replace(
            batch,
            status="BLOCKED_SCHEMA",
            validation_issues=issues,
            graph_fragment=None,
        )
        workspace.job.status = "BLOCKED_SCHEMA"
        workspace.job.stage = "awaiting_schema_approval"
        workspace.job.readiness_fingerprint = None
        return self._replace_batch(workspace, updated)

    # --------------------------------------------------------------------------
    # Đánh dấu đề xuất mở rộng ontology của batch bị từ chối (SCHEMA_REJECTED).
    #
    # Đầu vào (Input):
    #   - ingestion_id (str): Mã định danh phiên ingestion.
    #   - batch_index (int): Chỉ số index của batch bị từ chối đề xuất.
    #   - issues (list[dict]): Lý do từ chối hoặc các vấn đề liên quan.
    #
    # Đầu ra (Output):
    #   - Workspace: Workspace đã cập nhật trạng thái từ chối.
    # --------------------------------------------------------------------------
    async def reject_batch_schema_proposal(
        self, ingestion_id: str, batch_index: int, issues: list[dict]
    ) -> Workspace:
        workspace, batch = self._workspace_and_batch(ingestion_id, batch_index)
        updated = replace(
            batch,
            status="SCHEMA_REJECTED",
            validation_issues=issues,
            graph_fragment=None,
        )
        workspace.job.status = IngestionJobStatus.BATCHING
        workspace.job.stage = "schema_proposal_rejected"
        workspace.job.readiness_fingerprint = None
        return self._replace_batch(workspace, updated)

    # ==========================================================================
    # NHÓM 3: CHUYỂN ĐỔI TRẠNG THÁI JOB & HOÀN TẤT (Job State Transitions & Commit)
    # ==========================================================================

    # --------------------------------------------------------------------------
    # Đánh dấu toàn bộ các batch trong workspace đã STAGED và sẵn sàng ghi vào database (READY).
    #
    # Đầu vào (Input):
    #   - ingestion_id (str): Mã định danh phiên ingestion.
    #   - fingerprint (str): Mã vân tay xác thực tính toàn vẹn của đồ thị patch tổng thể.
    #
    # Đầu ra (Output):
    #   - Workspace: Workspace ở trạng thái READY.
    #
    # Lỗi (Raises):
    #   - RuntimeError: Nếu còn bất kỳ batch nào chưa đạt trạng thái STAGED.
    # --------------------------------------------------------------------------
    async def mark_ready(self, ingestion_id: str, fingerprint: str) -> Workspace:
        workspace = self._required(ingestion_id)
        if not workspace.batches or any(
            item.status != "STAGED" for item in workspace.batches
        ):
            raise RuntimeError("Every ingestion batch must be STAGED before finalize")
        workspace.job.status = IngestionJobStatus.READY
        workspace.job.stage = "ready_to_fill"
        workspace.job.readiness_fingerprint = fingerprint
        return workspace

    # --------------------------------------------------------------------------
    # Chuyển trạng thái workspace sang WRITING khi bắt đầu nạp các nodes/edges vào Neo4j.
    #
    # Đầu vào (Input):
    #   - ingestion_id (str): Mã định danh phiên ingestion.
    #
    # Đầu ra (Output):
    #   - Workspace: Workspace ở trạng thái WRITING.
    #
    # Lỗi (Raises):
    #   - RuntimeError: Nếu job chưa ở trạng thái READY hoặc thiếu readiness_fingerprint.
    # --------------------------------------------------------------------------
    async def mark_writing(self, ingestion_id: str) -> Workspace:
        workspace = self._required(ingestion_id)
        if (
            workspace.job.status != IngestionJobStatus.READY
            or not workspace.job.readiness_fingerprint
        ):
            raise RuntimeError("Ingestion must be finalized before writing")
        workspace.job.status = IngestionJobStatus.WRITING
        workspace.job.stage = "writing"
        return workspace

    # --------------------------------------------------------------------------
    # Đánh dấu hoàn tất toàn bộ quá trình nạp tài liệu thành công (COMMITTED).
    #
    # Tác dụng:
    #   Cập nhật thời gian hoàn tất, lưu version mới nhất của tài liệu và ghi nhận tóm tắt thống kê.
    #
    # Đầu vào (Input):
    #   - ingestion_id (str): Mã định danh phiên ingestion.
    #   - summary (dict): Thống kê tổng hợp số lượng nodes, edges và thuộc tính đã nạp.
    #
    # Đầu ra (Output):
    #   - Workspace: Workspace ở trạng thái COMMITTED.
    # --------------------------------------------------------------------------
    async def mark_committed(self, ingestion_id: str, summary: dict) -> Workspace:
        workspace = self._required(ingestion_id)
        now = datetime.now(timezone.utc).isoformat()
        workspace.job.status = IngestionJobStatus.COMMITTED
        workspace.job.stage = "committed"
        workspace.job.summary = {**summary, "committedAt": now}
        workspace = self._replace_version_status(
            workspace, SourceVersionStatus.COMMITTED
        )
        workspace = replace(
            workspace,
            document=replace(
                workspace.document, current_version_id=workspace.version.id
            ),
        )
        self._document_current_version[workspace.document.document_key] = (
            workspace.version.id
        )
        self._workspaces[str(workspace.job.id)] = workspace
        return workspace

    # --------------------------------------------------------------------------
    # Đánh dấu phiên ingestion thất bại kèm thông tin giai đoạn và nội dung lỗi.
    #
    # Đầu vào (Input):
    #   - ingestion_id (str): Mã định danh phiên ingestion.
    #   - stage (str): Tên bước/giai đoạn phát sinh lỗi (ví dụ: 'parsing', 'writing').
    #   - message (str): Thông điệp mô tả chi tiết lỗi.
    #
    # Đầu ra (Output):
    #   - Workspace: Workspace ở trạng thái FAILED.
    # --------------------------------------------------------------------------
    async def mark_failed(
        self, ingestion_id: str, stage: str, message: str
    ) -> Workspace:
        workspace = self._required(ingestion_id)
        workspace.job.status = IngestionJobStatus.FAILED
        workspace.job.stage = stage
        workspace.job.error_stage = stage
        workspace.job.error_message = message
        workspace = self._replace_version_status(workspace, SourceVersionStatus.FAILED)
        self._workspaces[str(workspace.job.id)] = workspace
        return workspace

    # ==========================================================================
    # NHÓM 4: QUẢN LÝ PHIÊN BẢN & REBASE ONTOLOGY (Document Versioning & Ontology Rebase)
    # ==========================================================================

    # --------------------------------------------------------------------------
    # Cập nhật trạng thái của một phiên bản tài liệu (DELETED, ROLLED_BACK, SUPERSEDED,...).
    #
    # Đầu vào (Input):
    #   - document_id (str): ID của tài liệu.
    #   - version_id (str | None): ID phiên bản cụ thể (nếu None sẽ lấy bản đầu tiên khớp document_id).
    #   - status (SourceVersionStatus): Trạng thái phiên bản mới cần cập nhật.
    #
    # Đầu ra (Output):
    #   - tuple[IngestionDocumentData, IngestionDocumentVersionData]: Cặp thông tin tài liệu và phiên bản đã sửa đổi.
    #
    # Lỗi (Raises):
    #   - KeyError: Nếu không tìm thấy tài liệu hoặc phiên bản tương ứng.
    # --------------------------------------------------------------------------
    async def set_document_version_status(
        self, document_id: str, version_id: str | None, status: SourceVersionStatus
    ) -> tuple[IngestionDocumentData, IngestionDocumentVersionData]:
        target = next(
            (
                item
                for item in self._workspaces.values()
                if str(item.document.id) == document_id
                and (version_id is None or str(item.version.id) == version_id)
            ),
            None,
        )
        if target is None:
            raise KeyError(version_id or document_id)
        updated = self._replace_version_status(target, status)
        if status in {SourceVersionStatus.DELETED, SourceVersionStatus.ROLLED_BACK}:
            updated = replace(
                updated, document=replace(updated.document, current_version_id=None)
            )
            self._document_current_version.pop(updated.document.document_key, None)
        self._workspaces[str(updated.job.id)] = updated
        return updated.document, updated.version

    # --------------------------------------------------------------------------
    # Chuyển đổi (rebase) toàn bộ workspace sang một phiên bản ontology đích mới.
    #
    # Tác dụng:
    #   Các batch mà scope snapshot hashes không thay đổi sẽ được giữ nguyên kết quả trích xuất
    #   (kèm cập nhật ontologyVersion). Các batch bị ảnh hưởng bởi thay đổi ontology sẽ được reset về PENDING
    #   để trích xuất lại.
    #
    # Đầu vào (Input):
    #   - ingestion_id (str): Mã định danh phiên ingestion.
    #   - target_version_id (str): ID phiên bản ontology mới cần rebase sang.
    #   - valid_snapshot_hashes (dict[str, str]): Bản đồ scope_key -> snapshot_hash hợp lệ của ontology mới.
    #   - merged_schema_hashes (dict[str, str]): Bản đồ scope_keys nối chuỗi -> merged_schema_hash mới.
    #
    # Đầu ra (Output):
    #   - Workspace: Workspace sau khi đã hoàn tất rebase sang phiên bản ontology mới.
    # --------------------------------------------------------------------------
    async def rebase_ontology_version(
        self,
        ingestion_id: str,
        target_version_id: str,
        valid_snapshot_hashes: dict[str, str],
        merged_schema_hashes: dict[str, str],
    ) -> Workspace:
        workspace = self._required(ingestion_id)
        target_uuid = to_uuid(target_version_id)
        batches: list[IngestionBatchData] = []
        for batch in workspace.batches:
            unchanged = bool(batch.scope_keys) and all(
                valid_snapshot_hashes.get(scope_key)
                == batch.snapshot_hashes.get(scope_key)
                for scope_key in batch.scope_keys
            )
            if unchanged and batch.graph_fragment:
                graph_fragment = {
                    **batch.graph_fragment,
                    "ontologyVersion": target_version_id,
                }
                batches.append(
                    replace(
                        batch,
                        graph_fragment=graph_fragment,
                        semantic_fragment={
                            **batch.semantic_fragment,
                            "ontologyVersion": target_version_id,
                        }
                        if batch.semantic_fragment
                        else None,
                        validation_issues=[],
                        merged_schema_hash=merged_schema_hashes.get(
                            "\x1f".join(batch.scope_keys),
                            batch.merged_schema_hash,
                        ),
                    )
                )
            else:
                batches.append(
                    replace(
                        batch,
                        status="PENDING",
                        validation_attempts=0,
                        graph_fragment=None,
                        semantic_fragment=None,
                        validation_issues=[],
                        merged_schema_hash=None,
                        scope_keys=[],
                        snapshot_hashes={},
                    )
                )
        workspace.job.ontology_version_id = target_uuid
        workspace.job.status = IngestionJobStatus.BATCHING
        workspace.job.stage = "batching"
        workspace.job.readiness_fingerprint = None
        workspace = replace(
            workspace,
            version=replace(
                workspace.version,
                ontology_version_id=target_uuid,
                ontology_digest=dict_digest({"versionId": target_version_id}),
            ),
            batches=tuple(batches),
        )
        self._workspaces[str(workspace.job.id)] = workspace
        return workspace

    # ==========================================================================
    # NHÓM 5: HÀM TRỢ GIÚP CẬP NHẬT DỮ LIỆU NỘI BỘ (Internal Mutation Helpers)
    # ==========================================================================

    # --------------------------------------------------------------------------
    # Hàm trợ giúp nội bộ lấy đối tượng Workspace theo ID hoặc ném lỗi nếu không tồn tại.
    #
    # Đầu vào (Input):
    #   - ingestion_id (str): Mã UUID của workspace.
    #
    # Đầu ra (Output):
    #   - Workspace: Đối tượng workspace tìm thấy.
    #
    # Lỗi (Raises):
    #   - KeyError: Nếu ingestion_id không tồn tại trong kho lưu trữ RAM.
    # --------------------------------------------------------------------------
    def _required(self, ingestion_id: str) -> Workspace:
        workspace = self._workspaces.get(str(ingestion_id))
        if workspace is None:
            raise KeyError(ingestion_id)
        return workspace

    # --------------------------------------------------------------------------
    # Hàm trợ giúp nội bộ lấy đồng thời Workspace và Batch tương ứng theo chỉ số batch_index.
    #
    # Đầu vào (Input):
    #   - ingestion_id (str): Mã UUID của workspace.
    #   - batch_index (int): Chỉ số thứ tự của batch trong workspace.
    #
    # Đầu ra (Output):
    #   - tuple[Workspace, IngestionBatchData]: Cặp đối tượng workspace và batch.
    #
    # Lỗi (Raises):
    #   - KeyError: Nếu workspace không tồn tại hoặc batch_index không hợp lệ.
    # --------------------------------------------------------------------------
    def _workspace_and_batch(
        self, ingestion_id: str, batch_index: int
    ) -> tuple[Workspace, IngestionBatchData]:
        workspace = self._required(ingestion_id)
        batch = next(
            (item for item in workspace.batches if item.batch_index == batch_index),
            None,
        )
        if batch is None:
            raise KeyError(f"Unknown batch index: {batch_index}")
        return workspace, batch

    # --------------------------------------------------------------------------
    # Hàm trợ giúp nội bộ cập nhật một batch trong workspace và lưu lại vào repository.
    #
    # Đầu vào (Input):
    #   - workspace (Workspace): Đối tượng workspace gốc.
    #   - batch (IngestionBatchData): Đối tượng batch mới đã chỉnh sửa.
    #
    # Đầu ra (Output):
    #   - Workspace: Đối tượng workspace mới sau khi thay thế batch.
    # --------------------------------------------------------------------------
    def _replace_batch(
        self, workspace: Workspace, batch: IngestionBatchData
    ) -> Workspace:
        updated = replace(
            workspace,
            batches=tuple(
                batch if item.batch_index == batch.batch_index else item
                for item in workspace.batches
            ),
        )
        self._workspaces[str(updated.job.id)] = updated
        return updated

    # --------------------------------------------------------------------------
    # Hàm trợ giúp nội bộ cập nhật trạng thái version của workspace.
    #
    # Đầu vào (Input):
    #   - workspace (Workspace): Đối tượng workspace gốc.
    #   - status (SourceVersionStatus): Trạng thái phiên bản mới.
    #
    # Đầu ra (Output):
    #   - Workspace: Bản sao workspace với version status đã được thay đổi.
    # --------------------------------------------------------------------------
    @staticmethod
    def _replace_version_status(
        workspace: Workspace, status: SourceVersionStatus
    ) -> Workspace:
        return replace(workspace, version=replace(workspace.version, status=status))
