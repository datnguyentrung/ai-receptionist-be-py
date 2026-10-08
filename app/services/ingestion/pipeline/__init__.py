"""Nhóm xử lý Pipeline Ingestion: nạp, phân tách văn bản và quản lý workspace theo phiên.

Danh sách các module trong package:
- `reader`: Tải và tiền xử lý tài liệu nguồn thông qua các chiến lược.
- `strategies`: Các chiến lược nạp (LoaderStrategy) và phân đoạn tài liệu (ChunkingStrategy).
- `staged_ingestion`: Quản lý vòng đời workspace, chia batch và hợp nhất graph fragments.
"""

from app.services.ingestion.pipeline.reader import (
    DocumentReader,
    DocumentReadError,
)
from app.services.ingestion.pipeline.staged_ingestion import (
    IngestionWorkspaceService,
    partition,
)
from app.services.ingestion.pipeline.strategies import (
    ChunkingStrategy,
    LoadedDocument,
    LoaderStrategy,
    StructuralTextChunker,
    Utf8TextLoader,
    canonicalize_markdown_plain_text,
    chunk_content_hash,
    preclean_document_text,
    stable_chunk_id,
    stable_document_id,
)

__all__ = [
    "ChunkingStrategy",
    "DocumentReadError",
    "DocumentReader",
    "IngestionWorkspaceService",
    "LoadedDocument",
    "LoaderStrategy",
    "StructuralTextChunker",
    "Utf8TextLoader",
    "canonicalize_markdown_plain_text",
    "chunk_content_hash",
    "partition",
    "preclean_document_text",
    "stable_chunk_id",
    "stable_document_id",
]
