"""Phase 2b — Chia tài liệu thành batch và quản lý workspace ingestion theo phiên.

Tài liệu dài được chia thành nhiều batch theo số chunk/số ký tự/số token ước lượng.
Workspace giữ fragment của từng batch, gộp dần thành một graph patch duy nhất và
kiểm tra fragment có đúng phạm vi batch của nó hay không.

Danh sách các hàm / phương thức trong module:
- `begin(...)`: Khởi tạo workspace mới cho tài liệu (chia batch, tính ingestion_id, lưu provenance & chunks).
- `submit(...)`: Tiếp nhận và ghi nhận GraphPatchFragment trả về từ LLM cho một batch cụ thể.
- `merged_patch(...)`: Hợp nhất toàn bộ fragment đã submit thành một GraphPatchDraft hoàn chỉnh.
- `next_batch(...)`: Lấy batch kế tiếp chưa hoàn thành (chưa STAGED) trong workspace.
- `is_current(...)`: Kiểm tra workspace có còn khớp với cấu hình provenance hiện tại hay không.
- `_partition(...)`: Hàm nội bộ gọi `partition` với các tham số cấu hình mặc định.
- `partition(...)`: Thuật toán chia danh sách chunks thành các IngestionBatch theo giới hạn số lượng, ký tự và token.
- `_validate_fragment_scope(...)`: Kiểm tra tính hợp lệ về phạm vi chunk/evidence của fragment so với batch.
- `_all_evidence(...)`: Trích xuất toàn bộ bằng chứng (Evidence) từ node, property và edge trong fragment.
- `_normalized_node_copy(...)`: Chuẩn hóa và gộp các thuộc tính trùng lặp trong nội bộ một node.
- `_merge_nodes(...)`: Hợp nhất danh sách node từ nhiều fragment, gán canonical ID và khử trùng thuộc tính.
- `_merge_edges(...)`: Hợp nhất danh sách quan hệ (edge), cập nhật ID nguồn/đích theo bảng ánh xạ node.
- `_canonical_temp_id(...)`: Tìm hoặc chọn temp_id chuẩn đại diện cho node dựa trên identity key.
- `_node_identity_key(...)`: Tạo tuple khóa định danh duy nhất cho node (class_name, identity_value).
- `_merge_coverage(...)`: Gộp danh sách xác nhận bao phủ chunk (ChunkCoverage) và kiểm tra mâu thuẫn quyết định.
- `_stable_value(...)`: Chuyển đổi giá trị bất kỳ thành chuỗi JSON chuẩn hóa để so sánh.
- `_merge_list_values(...)`: Gộp 2 danh sách giá trị và loại bỏ các phần tử trùng lặp.
- `_dedupe_models(...)`: Khử trùng danh sách Pydantic models dựa trên JSON hash.
"""

import hashlib
import logging
import math

from app.core.config import settings
from app.schemas.ingestion import (
    DocumentChunk,
    IngestionBatch,
    IngestionProvenance,
    IngestionWorkspace,
)

MAX_BATCH_CHUNKS = settings.INGESTION_MAX_BATCH_CHUNKS

# Chế độ cache theo từng chunk riêng lẻ:
# Khi bật ("1", "true", "yes", "on"), hệ thống ép mỗi batch chỉ chứa đúng 1 chunk (max_batch_chunks = 1)
# nhằm tối ưu hoá việc tái sử dụng cache kết quả trích xuất độc lập cho từng chunk
TRUE_CHUNK_CACHE_MODE = settings.INGESTION_TRUE_CHUNK_CACHE

# Giới hạn tổng số ký tự (characters) tối đa trong một batch (mặc định: 15.000 ký tự, tối thiểu: 1.000)
MAX_BATCH_CHARS = settings.INGESTION_MAX_BATCH_CHARS

# Tỷ lệ quy đổi ước lượng số ký tự trên mỗi token (mặc định: 2.0 ký tự/token, tối thiểu: 1.0)
# Dùng để tính nhanh: tokens = ceil(số_ký_tự / ESTIMATED_CHARS_PER_TOKEN)
ESTIMATED_CHARS_PER_TOKEN = settings.INGESTION_ESTIMATED_CHARS_PER_TOKEN

# Giới hạn tổng số token ước lượng tối đa cho một batch (mặc định: 15.000 tokens, tối thiểu: 1.000)
# Đảm bảo kích thước batch không vượt quá hạn mức context window / ngân sách token của LLM
MAX_BATCH_ESTIMATED_TOKENS = settings.INGESTION_MAX_BATCH_ESTIMATED_TOKENS

logger = logging.getLogger(__name__)


class WorkspaceConflictError(ValueError):
    """
    Lỗi khi thao tác không khớp với workspace hiện tại (ví dụ batch đã submit).

    Args:
        message: Mô tả xung đột.
        conflict: Chi tiết xung đột kèm theo (tuỳ chọn).
    """

    def __init__(self, message: str, *, conflict: dict | None = None):
        """
        Ghi nhận message và chi tiết xung đột.

        Args:
            message: Mô tả xung đột.
            conflict: Dict chi tiết, mặc định là None.
        """
        super().__init__(message)
        self.conflict = conflict or {}


class IngestionWorkspaceService:
    """
    Quản lý vòng đời workspace ingestion: chia batch, nhận fragment, gộp patch.
    """

    def begin(
        self,
        *,
        artifact_name: str,
        provenance: IngestionProvenance,
        chunks: list[DocumentChunk],
    ) -> IngestionWorkspace:
        """
        Tạo workspace mới cho một tài liệu: chia batch và ghi provenance.

        Args:
            artifact_name: Tên tài liệu nguồn.
            provenance: Thông tin phiên bản nguồn/ontology/skill.
            chunks: Toàn bộ chunk của tài liệu.

        Returns:
            `IngestionWorkspace` đã chia batch.
        """
        batches = self._partition(chunks)
        identity_material = provenance.identity_material(artifact_name)
        ingestion_id = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()
        logger.info(
            "INGESTION_DOCUMENT ingestion_id=%s document=%s total_chunks=%s batch_count=%s",
            ingestion_id,
            artifact_name,
            len(chunks),
            len(batches),
        )
        chunk_by_index = {chunk.index: chunk for chunk in chunks}
        for batch in batches:
            estimated_tokens = max(
                1, math.ceil(batch.content_chars / ESTIMATED_CHARS_PER_TOKEN)
            )
            logger.info(
                "[INGESTION_BATCH_CREATED] ingestion_id=%s batch=%s chunk_ids=%s input_chars=%s estimated_tokens=%s",
                ingestion_id,
                batch.index,
                batch.chunk_indexes,
                batch.content_chars,
                estimated_tokens,
            )
            for chunk_index in batch.chunk_indexes:
                chunk = chunk_by_index[chunk_index]
                logger.debug(
                    "[INGESTION_BATCH_CREATED] ingestion_id=%s batch=%s chunk=%s "
                    "section=%r line_start=%s line_end=%s char_count=%s",
                    ingestion_id,
                    batch.index,
                    chunk.index,
                    chunk.section,
                    chunk.start_line,
                    chunk.end_line,
                    len(chunk.content),
                )
        return IngestionWorkspace(
            ingestionId=ingestion_id,
            artifactName=artifact_name,
            provenance=provenance,
            chunks=chunks,
            batches=batches,
        )

    @staticmethod
    def _partition(chunks: list[DocumentChunk]) -> list[IngestionBatch]:
        """
        Hàm bao bọc (wrapper) gọi `partition` với các giới hạn batch cấu hình mặc định.

        Args:
            chunks (list[DocumentChunk]): Danh sách các chunk nguồn.

        Returns:
            list[IngestionBatch]: Danh sách các batch đã phân chia.
        """
        return IngestionWorkspaceService.partition(
            chunks,
            max_batch_chunks=MAX_BATCH_CHUNKS,
            max_batch_chars=MAX_BATCH_CHARS,
        )

    @staticmethod
    def partition(
        chunks: list[DocumentChunk],
        *,
        max_batch_chunks: int,
        max_batch_chars: int,
    ) -> list[IngestionBatch]:
        """
        Chia danh sách chunk thành các batch theo giới hạn số lượng, ký tự và token ước lượng.

        Args:
            chunks (list[DocumentChunk]): Danh sách toàn bộ chunk cần phân bổ.
            max_batch_chunks (int): Số lượng chunk tối đa trong 1 batch (nếu TRUE_CHUNK_CACHE_MODE bật sẽ luôn là 1).
            max_batch_chars (int): Số ký tự tối đa cho phép trong 1 batch.

        Returns:
            list[IngestionBatch]: Danh sách các IngestionBatch kèm theo danh sách chỉ số chunk_indexes.

        Raises:
            ValueError: Nếu danh sách chunks rỗng, hoặc có chunk đơn lẻ vượt quá ngân sách batch.
        """
        if not chunks:
            raise ValueError("At least one source chunk is required")
        batches: list[IngestionBatch] = []
        current: list[DocumentChunk] = []
        current_chars = 0
        current_tokens = 0
        max_batch_chunks = 1 if TRUE_CHUNK_CACHE_MODE else max(1, max_batch_chunks)
        max_batch_chars = max(1_000, max_batch_chars)
        for chunk in chunks:
            chunk_chars = len(chunk.content)
            chunk_tokens = max(1, math.ceil(chunk_chars / ESTIMATED_CHARS_PER_TOKEN))
            if (
                chunk_chars > max_batch_chars
                or chunk_tokens > MAX_BATCH_ESTIMATED_TOKENS
            ):
                raise ValueError(
                    f"Chunk {chunk.index} exceeds configured batch budget "
                    f"({chunk_chars} chars, ~{chunk_tokens} tokens)"
                )
            would_overflow = (
                len(current) >= max_batch_chunks
                or current_chars + chunk_chars > max_batch_chars
                or current_tokens + chunk_tokens > MAX_BATCH_ESTIMATED_TOKENS
            )
            if current and would_overflow:
                batches.append(
                    IngestionBatch(
                        index=len(batches),
                        chunkIndexes=[item.index for item in current],
                        contentChars=current_chars,
                    )
                )
                current = []
                current_chars = 0
                current_tokens = 0
            current.append(chunk)
            current_chars += chunk_chars
            current_tokens += chunk_tokens
        if current:
            batches.append(
                IngestionBatch(
                    index=len(batches),
                    chunkIndexes=[item.index for item in current],
                    contentChars=current_chars,
                )
            )
        return batches


def partition(
    chunks: list[DocumentChunk],
    *,
    max_batch_chunks: int = MAX_BATCH_CHUNKS,
    max_batch_chars: int = MAX_BATCH_CHARS,
) -> list[IngestionBatch]:
    """Partition chunks through the pipeline's established batching policy."""

    return IngestionWorkspaceService.partition(
        chunks,
        max_batch_chunks=max_batch_chunks,
        max_batch_chars=max_batch_chars,
    )
