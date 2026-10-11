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
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.enums import IngestionJobStatus, SourceVersionStatus
from app.models import IngestionWorkspaceCheckpoint
from app.models import IngestionFactConflict
from app.models import IngestionCommitApproval, IngestionKnowledgeRevision
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
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession] | None = None, *_: Any, **__: Any
    ) -> None:
        self._session_factory = session_factory
        self._workspaces: dict[str, Workspace] = {}
        self._by_signature: dict[str, str] = {}
        self._document_current_version: dict[str, uuid.UUID] = {}
        self._local_revision = 0
        self._local_conflicts: dict[str, list[dict[str, Any]]] = {}

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
                "documentKey": document_key,
                "contentHash": content_hash,
                "configSignature": config_signature,
                "skillDigest": skill_digest,
                "modelId": model_id,
                "mapperVersion": MAPPER_VERSION,
                "compilerVersion": compiler_version,
            }
        )
        existing_id = self._by_signature.get(ingestion_signature)
        if existing_id is None:
            existing_id = await self._load_signature(ingestion_signature)
        if existing_id:
            workspace = self._workspaces[existing_id]
            committed = workspace.job.status == IngestionJobStatus.COMMITTED
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
            current_version_id=await self._current_version(document_key),
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
        await self._persist(workspace, ingestion_signature)
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
        workspace = self._workspaces.get(str(ingestion_id))
        if workspace is not None or self._session_factory is None:
            return workspace
        async with self._session_factory() as session:
            row = await session.get(IngestionWorkspaceCheckpoint, uuid.UUID(str(ingestion_id)))
        if row is None:
            return None
        workspace = _workspace_from_payload(row.payload)
        self._workspaces[str(workspace.job.id)] = workspace
        self._by_signature[row.ingestion_signature] = str(workspace.job.id)
        return workspace

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
        if self._session_factory is not None:
            async with self._session_factory() as session:
                rows = list((await session.scalars(select(IngestionWorkspaceCheckpoint).where(
                    IngestionWorkspaceCheckpoint.payload["job"]["status"].as_string() == "COMMITTED"
                ).order_by(IngestionWorkspaceCheckpoint.updated_at.desc()).limit(limit))).all())
            return [item for row in rows
                    if (item := _workspace_from_payload(row.payload))
                    and (document_id is None or str(item.document.id) == document_id)]
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
        return await self._save(self._replace_batch(workspace, updated))

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
        return await self._save(self._replace_batch(workspace, updated))

    async def restart_from_batch(
        self, ingestion_id: str, batch_index: int, reason: str
    ) -> Workspace:
        """Discard staged extraction from one batch onward after an extractor fix."""
        workspace = await self.get_workspace(ingestion_id)
        if workspace is None:
            raise KeyError(ingestion_id)
        restarted = []
        for batch in workspace.batches:
            if batch.batch_index < batch_index:
                restarted.append(batch)
                continue
            restarted.append(
                replace(
                    batch,
                    status="PENDING",
                    validation_attempts=0,
                    validation_issues=[],
                    scope_keys=[],
                    snapshot_hashes={},
                    merged_schema_hash=None,
                    semantic_fragment=None,
                    graph_fragment=None,
                    validated_baseline=None,
                )
            )
        workspace = replace(workspace, batches=restarted)
        workspace.job.status = IngestionJobStatus.BATCHING
        workspace.job.stage = "batching"
        workspace.job.error_stage = None
        workspace.job.error_message = reason
        workspace.job.readiness_fingerprint = None
        return await self._save(workspace)

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
        return await self._save(self._replace_batch(workspace, updated))

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
        return await self._save(self._replace_batch(workspace, updated))

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
        return await self._save(workspace)

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
        return await self._save(workspace)

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
        workspace.job.error_stage = None
        workspace.job.error_message = None
        workspace.job.summary = {**workspace.job.summary, **summary, "committedAt": now, "commitPhase": "COMMITTED"}
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
        return await self._save(workspace)

    async def store_preview(self, ingestion_id: str, report: dict[str, Any]) -> dict[str, Any]:
        workspace = self._required(ingestion_id)
        if workspace.job.status not in {
            IngestionJobStatus.READY, IngestionJobStatus.AWAITING_COMMIT_APPROVAL
        }:
            raise RuntimeError("Finalize all batches before creating an ingestion preview")
        workspace.job.status = IngestionJobStatus.AWAITING_COMMIT_APPROVAL
        workspace.job.stage = "awaiting_commit_approval"
        workspace.job.summary = {
            **workspace.job.summary,
            "preview": report,
            "approvedDigest": None,
            "commitPhase": None,
        }
        await self._save(workspace)
        return await self.get_preview(ingestion_id)

    async def record_runtime_metadata(
        self, ingestion_id: str, *, model_assignments: dict[str, str], contract_hash: str
    ) -> Workspace:
        """Persist the execution configuration that produced the staged data."""
        workspace = self._required(ingestion_id)
        workspace.job.summary = {
            **workspace.job.summary,
            "modelAssignments": model_assignments,
            "contractHash": contract_hash,
        }
        return await self._save(workspace)

    async def get_preview(self, ingestion_id: str) -> dict[str, Any]:
        workspace = self._required(ingestion_id)
        report = workspace.job.summary.get("preview")
        if not isinstance(report, dict):
            raise RuntimeError("No ingestion preview exists")
        conflicts = await self.list_fact_conflicts(ingestion_id)
        digest = dict_digest({
            "report": report,
            "decisions": [
                {"id": item["conflictId"], "decision": item["decision"], "note": item.get("note")}
                for item in conflicts
            ],
        })
        workspace.job.summary["reportDigest"] = digest
        await self._save(workspace)
        return {
            "success": True,
            "stage": "awaiting_commit_approval",
            "terminal": False,
            "status": IngestionJobStatus.AWAITING_COMMIT_APPROVAL.value,
            "nextAction": "review_preview_and_confirm",
            **report,
            "reportDigest": digest,
            "conflicts": conflicts,
            "pendingConflictCount": sum(item["decision"] == "PENDING" for item in conflicts),
        }

    async def record_commit_approval(
        self, ingestion_id: str, report_digest: str, *, user_id: str,
        session_id: str, function_call_id: str,
    ) -> None:
        workspace = await self.get_workspace(ingestion_id)
        if workspace is None:
            raise KeyError(ingestion_id)
        if workspace.job.status != IngestionJobStatus.AWAITING_COMMIT_APPROVAL:
            raise RuntimeError("Ingestion is not awaiting commit approval")
        preview = await self.get_preview(ingestion_id)
        if preview["reportDigest"] != report_digest:
            raise ValueError("Preview changed; review the new report before committing")
        if preview["pendingConflictCount"]:
            raise ValueError("Every conflict needs CONFIRM, REJECT, or OTHER")
        if not user_id or not session_id or not function_call_id:
            raise PermissionError("Trusted ADK confirmation context is required")
        if self._session_factory is not None:
            async with self._session_factory() as session, session.begin():
                session.add(IngestionCommitApproval(
                    ingestion_id=uuid.UUID(ingestion_id), report_digest=report_digest,
                    user_id=user_id, session_id=session_id,
                    function_call_id=function_call_id,
                ))
        workspace.job.summary["approvedDigest"] = report_digest
        workspace.job.summary["approvalUserId"] = user_id
        await self._save(workspace)

    async def has_commit_approval(self, ingestion_id: str, report_digest: str) -> bool:
        workspace = self._required(ingestion_id)
        if workspace.job.summary.get("approvedDigest") != report_digest:
            return False
        if self._session_factory is None:
            return True
        async with self._session_factory() as session:
            row = await session.scalar(select(IngestionCommitApproval).where(
                IngestionCommitApproval.ingestion_id == uuid.UUID(ingestion_id),
                IngestionCommitApproval.report_digest == report_digest,
            ).order_by(IngestionCommitApproval.confirmed_at.desc()))
            return row is not None

    async def set_commit_phase(self, ingestion_id: str, phase: str) -> Workspace:
        workspace = self._required(ingestion_id)
        workspace.job.status = IngestionJobStatus.COMMITTING
        workspace.job.stage = phase.casefold()
        workspace.job.summary["commitPhase"] = phase
        return await self._save(workspace)

    async def abort(self, ingestion_id: str) -> Workspace:
        workspace = self._required(ingestion_id)
        if workspace.job.status == IngestionJobStatus.COMMITTED:
            raise RuntimeError("Committed ingestion cannot be aborted")
        if workspace.job.status == IngestionJobStatus.COMMITTING:
            raise RuntimeError("Commit is in progress; reconcile it instead")
        workspace.job.status = IngestionJobStatus.ABORTED
        workspace.job.stage = "aborted"
        workspace.job.summary.pop("preview", None)
        workspace.job.summary.pop("reportDigest", None)
        workspace = await self._save(workspace)
        signature = next((key for key, value in self._by_signature.items()
                          if value == ingestion_id), None)
        if signature is not None:
            self._by_signature.pop(signature, None)
            if self._session_factory is not None:
                async with self._session_factory() as session, session.begin():
                    row = await session.get(IngestionWorkspaceCheckpoint, uuid.UUID(ingestion_id))
                    if row is not None:
                        row.ingestion_signature = dict_digest({
                            "abortedSignature": signature, "jobId": ingestion_id
                        })
        return workspace

    async def knowledge_revision(self) -> int:
        if self._session_factory is None:
            return self._local_revision
        async with self._session_factory() as session:
            row = await session.get(IngestionKnowledgeRevision, 1)
            return row.revision if row else 0

    async def unfinished_commits(self) -> list[str]:
        if self._session_factory is None:
            return [key for key, item in self._workspaces.items()
                    if item.job.status == IngestionJobStatus.COMMITTING]
        async with self._session_factory() as session:
            rows = (await session.scalars(select(IngestionWorkspaceCheckpoint).where(
                IngestionWorkspaceCheckpoint.payload["job"]["status"].as_string() == "COMMITTING"
            ))).all()
            return [str(row.job_id) for row in rows]

    async def advance_knowledge_revision(self, ingestion_id: str) -> int:
        if self._session_factory is None:
            if getattr(self, "_local_last_revision_job", None) == ingestion_id:
                return self._local_revision
            self._local_revision += 1
            self._local_last_revision_job = ingestion_id
            return self._local_revision
        async with self._session_factory() as session, session.begin():
            row = await session.get(IngestionKnowledgeRevision, 1, with_for_update=True)
            if row is None:
                row = IngestionKnowledgeRevision(id=1, revision=0)
                session.add(row)
            if row.last_ingestion_id == uuid.UUID(ingestion_id):
                return row.revision
            row.revision += 1
            row.last_ingestion_id = uuid.UUID(ingestion_id)
            return row.revision

    @asynccontextmanager
    async def commit_lock(self):
        """Serialize publication across jobs; PostgreSQL releases this lock on crash."""
        if self._session_factory is None:
            yield
            return
        async with self._session_factory() as session:
            await session.execute(text("SELECT pg_advisory_lock(894357102)"))
            try:
                yield
            finally:
                await session.execute(text("SELECT pg_advisory_unlock(894357102)"))

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
        return await self._save(workspace)

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
        await self._save(updated)
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
        return await self._save(workspace)

    async def _save(self, workspace: Workspace) -> Workspace:
        self._workspaces[str(workspace.job.id)] = workspace
        await self._persist(workspace)
        return workspace

    async def _persist(self, workspace: Workspace, signature: str | None = None) -> None:
        if self._session_factory is None:
            return
        signature = signature or next(
            (key for key, value in self._by_signature.items() if value == str(workspace.job.id)),
            None,
        )
        if not signature:
            return
        payload = _workspace_payload(workspace)
        async with self._session_factory() as session, session.begin():
            row = await session.get(IngestionWorkspaceCheckpoint, workspace.job.id)
            if row is None:
                session.add(IngestionWorkspaceCheckpoint(
                    job_id=workspace.job.id,
                    ingestion_signature=signature,
                    document_key=workspace.document.document_key,
                    payload=payload,
                ))
            else:
                row.payload = payload
                row.document_key = workspace.document.document_key

    async def _load_signature(self, signature: str) -> str | None:
        if self._session_factory is None:
            return None
        async with self._session_factory() as session:
            row = await session.scalar(select(IngestionWorkspaceCheckpoint).where(
                IngestionWorkspaceCheckpoint.ingestion_signature == signature
            ))
        if row is None:
            return None
        workspace = _workspace_from_payload(row.payload)
        self._workspaces[str(workspace.job.id)] = workspace
        self._by_signature[signature] = str(workspace.job.id)
        return str(workspace.job.id)

    async def _current_version(self, document_key: str, *, fresh: bool = False) -> uuid.UUID | None:
        cached = self._document_current_version.get(document_key)
        if not fresh and (cached is not None or self._session_factory is None):
            return cached
        if self._session_factory is None:
            current = next((item.version.id for item in reversed(list(self._workspaces.values()))
                            if item.document.document_key == document_key
                            and item.version.status == SourceVersionStatus.COMMITTED), None)
            return current
        async with self._session_factory() as session:
            rows = list((await session.scalars(select(IngestionWorkspaceCheckpoint).where(
                IngestionWorkspaceCheckpoint.document_key == document_key
            ).order_by(IngestionWorkspaceCheckpoint.updated_at.desc()))).all())
        for row in rows:
            workspace = _workspace_from_payload(row.payload)
            if workspace.version.status == SourceVersionStatus.COMMITTED:
                self._document_current_version[document_key] = workspace.version.id
                return workspace.version.id
        self._document_current_version.pop(document_key, None)
        return None

    async def refresh_document_current(self, ingestion_id: str) -> Workspace:
        workspace = self._required(ingestion_id)
        current_id = await self._current_version(workspace.document.document_key, fresh=True)
        if current_id != workspace.document.current_version_id:
            workspace = replace(workspace, document=replace(
                workspace.document, current_version_id=current_id
            ))
            await self._save(workspace)
        return workspace

    async def mark_superseded(self, document_key: str, version_id: str) -> None:
        """Retain the old checkpoint while removing it from the current view."""
        if self._session_factory is None:
            for item in self._workspaces.values():
                if item.document.document_key == document_key and str(item.version.id) == version_id:
                    updated = self._replace_version_status(item, SourceVersionStatus.SUPERSEDED)
                    self._workspaces[str(updated.job.id)] = updated
                    return
            return
        async with self._session_factory() as session, session.begin():
            rows = list((await session.scalars(select(IngestionWorkspaceCheckpoint).where(
                IngestionWorkspaceCheckpoint.document_key == document_key
            ))).all())
            for row in rows:
                item = _workspace_from_payload(row.payload)
                if str(item.version.id) != version_id:
                    continue
                updated = self._replace_version_status(item, SourceVersionStatus.SUPERSEDED)
                row.payload = _workspace_payload(updated)
                self._workspaces[str(updated.job.id)] = updated
                self._by_signature[row.ingestion_signature] = str(updated.job.id)
                return

    async def record_fact_conflicts(
        self, ingestion_id: str, conflicts: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Upsert conflict candidates created by a committed document version."""
        if self._session_factory is None:
            old = {item["conflictId"]: item for item in self._local_conflicts.get(ingestion_id, [])}
            rows = []
            for item in conflicts:
                conflict_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{item['newFactId']}:{item['oldFactId']}"))
                rows.append({"conflictId": conflict_id, "ingestionId": ingestion_id,
                             "decision": old.get(conflict_id, {}).get("decision", "PENDING"),
                             "note": old.get(conflict_id, {}).get("note"), **item})
            self._local_conflicts[ingestion_id] = rows
            return rows
        async with self._session_factory() as session, session.begin():
            persisted: list[dict[str, Any]] = []
            pairs = {(item["newFactId"], item["oldFactId"]) for item in conflicts}
            old_rows = list((await session.scalars(select(IngestionFactConflict).where(
                IngestionFactConflict.ingestion_id == uuid.UUID(ingestion_id)
            ))).all())
            for old in old_rows:
                if (old.new_fact_id, old.old_fact_id) not in pairs:
                    await session.delete(old)
            for details in conflicts:
                row = await session.scalar(select(IngestionFactConflict).where(
                    IngestionFactConflict.new_fact_id == details["newFactId"],
                    IngestionFactConflict.old_fact_id == details["oldFactId"],
                ))
                if row is None:
                    row = IngestionFactConflict(
                        ingestion_id=uuid.UUID(ingestion_id), new_fact_id=details["newFactId"],
                        old_fact_id=details["oldFactId"], details=details,
                    )
                    session.add(row)
                    await session.flush()
                else:
                    row.details = details
                persisted.append({"conflictId": str(row.id), "decision": row.decision, **row.details})
            return persisted

    async def list_fact_conflicts(self, ingestion_id: str) -> list[dict[str, Any]]:
        if self._session_factory is None:
            return list(self._local_conflicts.get(ingestion_id, []))
        async with self._session_factory() as session:
            rows = list((await session.scalars(select(IngestionFactConflict).where(
                IngestionFactConflict.ingestion_id == uuid.UUID(ingestion_id)
            ).order_by(IngestionFactConflict.created_at))).all())
        return [{"conflictId": str(row.id), "decision": row.decision, "note": row.note, **row.details} for row in rows]

    async def review_fact_conflict(
        self, conflict_id: str, decision: str, note: str | None = None
    ) -> dict[str, Any]:
        normalized = decision.strip().upper()
        if normalized not in {"CONFIRM", "REJECT", "OTHER"}:
            raise ValueError("decision must be CONFIRM, REJECT, or OTHER")
        if normalized == "OTHER" and not (note or "").strip():
            raise ValueError("OTHER requires a note explaining the unresolved conflict")
        if self._session_factory is None:
            for ingestion_id, rows in self._local_conflicts.items():
                for row in rows:
                    if row["conflictId"] == conflict_id:
                        row["decision"], row["note"] = normalized, note
                        workspace = self._required(ingestion_id)
                        workspace.job.summary["approvedDigest"] = None
                        if workspace.job.summary.get("preview"):
                            await self.get_preview(ingestion_id)
                        return row
            raise KeyError(conflict_id)
        async with self._session_factory() as session, session.begin():
            row = await session.get(IngestionFactConflict, uuid.UUID(conflict_id), with_for_update=True)
            if row is None:
                raise KeyError(conflict_id)
            row.decision, row.note, row.reviewed_at = normalized, note, datetime.now(timezone.utc)
            result = {"conflictId": str(row.id), "ingestionId": str(row.ingestion_id), "decision": row.decision, "note": row.note, **row.details}
        workspace = await self.get_workspace(str(row.ingestion_id))
        if workspace and workspace.job.summary.get("preview"):
            workspace.job.summary["approvedDigest"] = None
            await self._save(workspace)
            await self.get_preview(str(row.ingestion_id))
        return result

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


def _workspace_payload(workspace: Workspace) -> dict[str, Any]:
    """Turn dataclasses into JSONB without losing UUIDs or enum values."""
    def convert(value: Any) -> Any:
        if isinstance(value, uuid.UUID):
            return str(value)
        if hasattr(value, "value"):
            return value.value
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(item) for item in value]
        return value
    return convert(asdict(workspace))


def _workspace_from_payload(payload: dict[str, Any]) -> Workspace:
    """Rebuild the internal workspace contract from its durable JSONB snapshot."""
    document = payload["document"]
    version = payload["version"]
    job = payload["job"]
    return Workspace(
        document=IngestionDocumentData(
            id=uuid.UUID(document["id"]), document_key=document["document_key"],
            name=document["name"],
            current_version_id=uuid.UUID(document["current_version_id"])
            if document.get("current_version_id") else None,
        ),
        version=IngestionDocumentVersionData(
            id=uuid.UUID(version["id"]), document_id=uuid.UUID(version["document_id"]),
            content_hash=version["content_hash"],
            ontology_version_id=uuid.UUID(version["ontology_version_id"]),
            ontology_digest=version["ontology_digest"],
            status=SourceVersionStatus(version["status"]),
        ),
        job=IngestionJobData(
            id=uuid.UUID(job["id"]), document_version_id=uuid.UUID(job["document_version_id"]),
            ontology_version_id=uuid.UUID(job["ontology_version_id"]),
            status=IngestionJobStatus(job["status"]), stage=job["stage"],
            scope_hint=job.get("scope_hint"), readiness_fingerprint=job.get("readiness_fingerprint"),
            error_stage=job.get("error_stage"), error_message=job.get("error_message"),
            summary=job.get("summary") or {},
        ),
        chunks=tuple(IngestionChunkData(
            id=uuid.UUID(item["id"]), document_version_id=uuid.UUID(item["document_version_id"]),
            chunk_id=item["chunk_id"], chunk_index=item["chunk_index"], text=item["text"],
            content_hash=item["content_hash"], token_count=item["token_count"],
            section=item.get("section"), page_start=item.get("page_start"),
            page_end=item.get("page_end"), source_anchor=item["source_anchor"],
        ) for item in payload["chunks"]),
        batches=tuple(IngestionBatchData(
            id=uuid.UUID(item["id"]), job_id=uuid.UUID(item["job_id"]),
            batch_index=item["batch_index"], chunk_indexes=item["chunk_indexes"],
            status=item["status"], semantic_fragment=item.get("semantic_fragment"),
            graph_fragment=item.get("graph_fragment"), validation_issues=item.get("validation_issues") or [],
            validation_attempts=item["validation_attempts"], merged_schema_hash=item.get("merged_schema_hash"),
            scope_keys=item.get("scope_keys") or [], snapshot_hashes=item.get("snapshot_hashes") or {},
            validated_baseline=item.get("validated_baseline"),
        ) for item in payload["batches"]),
    )
