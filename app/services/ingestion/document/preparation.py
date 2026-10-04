"""Phase 1 — Document preparation coordinator."""

import logging
from pathlib import Path

from app.core.schemas.ingestion.document import DocumentChunk
from app.services.ingestion.document.reader import DocumentReader

logger = logging.getLogger(__name__)


class DocumentPreparation:
    """Coordinate reading, preprocessing, and chunking of ingestion documents."""

    def __init__(self, reader: DocumentReader | None = None) -> None:
        self.reader = reader or DocumentReader()

    def prepare_path(self, path: str | Path) -> list[DocumentChunk]:
        return self.reader.read(path)

    def prepare_bytes(
        self,
        *,
        filename: str,
        data: bytes,
        mime_type: str | None = None,
    ) -> list[DocumentChunk]:
        return self.reader.read_bytes(
            filename=filename,
            data=data,
            mime_type=mime_type,
        )


__all__ = ["DocumentPreparation"]
