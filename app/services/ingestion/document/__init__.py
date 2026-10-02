"""Document ingestion module."""

from app.services.ingestion.document.preparation import DocumentPreparation
from app.services.ingestion.document.reader import (
    DocumentReadError,
    DocumentReader,
)
from app.services.ingestion.document.strategies import (
    DOCUMENT_ID_VERSION,
    STRUCTURAL_CHUNKER_VERSION,
    ChunkingStrategy,
    LoadedDocument,
    LoaderStrategy,
    StructuralTextChunker,
    Utf8TextLoader,
    chunk_content_hash,
    stable_chunk_id,
    stable_document_id,
)

__all__ = [
    "DOCUMENT_ID_VERSION",
    "STRUCTURAL_CHUNKER_VERSION",
    "ChunkingStrategy",
    "DocumentPreparation",
    "DocumentReadError",
    "DocumentReader",
    "LoadedDocument",
    "LoaderStrategy",
    "StructuralTextChunker",
    "Utf8TextLoader",
    "chunk_content_hash",
    "stable_chunk_id",
    "stable_document_id",
]
