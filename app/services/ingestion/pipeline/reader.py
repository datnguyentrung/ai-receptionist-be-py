"""Phase 1 — Tải và tiền xử lý tài liệu nguồn thông qua các chiến lược thay thế được.

Module này chịu trách nhiệm nạp tài liệu từ đường dẫn tệp tin hoặc dữ liệu nhị phân (bytes),
áp dụng bộ tiền xử lý Markdown và chia tài liệu thành danh sách các đoạn văn bản (DocumentChunk).

Danh sách các hàm / phương thức trong module:
- `DocumentReadError`: Ngoại lệ khi không thể đọc hoặc nạp tài liệu an toàn.
- `DocumentReader.__init__(...)`: Khởi tạo reader với các chiến lược Loader, Chunker và Preprocessor.
- `DocumentReader.read(...)`: Đọc và chia chunk tài liệu từ đường dẫn file trên ổ đĩa.
- `DocumentReader.read_bytes(...)`: Đọc và chia chunk tài liệu từ dữ liệu byte tải lên.
- `DocumentReader._split(...)`: Phương thức nội bộ thực hiện tiền xử lý Markdown và chia đoạn văn bản.
"""

import logging
from pathlib import Path

from app.schemas.ingestion import DocumentChunk
from app.scripts.preprocessing.markdown_preprocessor import (
    MarkdownPreprocessor,
)
from app.services.ingestion.pipeline.strategies import (
    ChunkingStrategy,
    LoadedDocument,
    LoaderStrategy,
    StructuralTextChunker,
    Utf8TextLoader,
)

logger = logging.getLogger(__name__)


class DocumentReadError(ValueError):
    """
    Ngoại lệ nghiệp vụ khi tài liệu nguồn không thể tải hoặc xử lý an toàn.
    """


class DocumentReader:
    """
    Tải và chia nhỏ tài liệu nguồn mà không phụ thuộc cứng vào định dạng tệp tin.
    """

    def __init__(
        self,
        *,
        loader: LoaderStrategy | None = None,
        chunker: ChunkingStrategy | None = None,
        preprocessor: MarkdownPreprocessor | None = None,
    ) -> None:
        """
        Khởi tạo DocumentReader với các chiến lược tùy biến.

        Args:
            loader: Chiến lược đọc file (mặc định Utf8TextLoader).
            chunker: Chiến lược phân tách chunk (mặc định StructuralTextChunker).
            preprocessor: Bộ tiền xử lý Markdown (mặc định MarkdownPreprocessor).
        """
        # 1. Gán chiến lược đọc file hoặc dùng Utf8TextLoader mặc định
        self.loader = loader or Utf8TextLoader()
        # 2. Gán chiến lược chia chunk cấu trúc hoặc dùng StructuralTextChunker mặc định
        self.chunker = chunker or StructuralTextChunker()
        # 3. Gán bộ tiền xử lý Markdown hoặc dùng MarkdownPreprocessor mặc định
        self.preprocessor = preprocessor or MarkdownPreprocessor()
        # 4. Lưu tập hợp các đuôi file được hỗ trợ
        self.SUPPORTED_SUFFIXES = set(self.loader.supported_suffixes)

    def read(self, path: str | Path) -> list[DocumentChunk]:
        """
        Đọc và phân tách tài liệu từ đường dẫn tệp tin trên hệ thống.

        Args:
            path: Đường dẫn tệp tin nguồn (str hoặc Path).

        Returns:
            list[DocumentChunk]: Danh sách các đoạn văn bản (chunks) đã trích xuất.

        Raises:
            FileNotFoundError: Nếu tệp tin không tồn tại.
            DocumentReadError: Nếu đường dẫn không phải tệp hoặc xảy ra lỗi đọc file.
        """
        # 1. Chuẩn hóa đường dẫn thành đối tượng Path
        document_path = Path(path)
        logger.info("Reading ingestion document path=%s", document_path)

        # 2. Kiểm tra tệp tin có tồn tại không
        if not document_path.exists():
            raise FileNotFoundError(f"Document not found: {document_path}")

        # 3. Kiểm tra đường dẫn có phải là file hợp lệ không
        if not document_path.is_file():
            raise DocumentReadError(f"Document path is not a file: {document_path}")

        # 4. Nạp nội dung tài liệu qua loader
        try:
            document = self.loader.load_path(document_path)
        except (OSError, UnicodeError, ValueError) as exc:
            raise DocumentReadError(str(exc)) from exc

        # 5. Phân đoạn tài liệu thành các chunks
        return self._split(document)

    def read_bytes(
        self,
        *,
        filename: str,
        data: bytes,
        mime_type: str | None = None,
    ) -> list[DocumentChunk]:
        """
        Đọc và phân tách tài liệu từ mảng byte nhị phân tải lên.

        Args:
            filename: Tên tệp tin gốc.
            data: Dữ liệu nhị phân của tài liệu.
            mime_type: Định dạng MIME tùy chọn của tài liệu.

        Returns:
            list[DocumentChunk]: Danh sách các đoạn văn bản (chunks) đã trích xuất.

        Raises:
            DocumentReadError: Nếu xảy ra lỗi giải mã dữ liệu nhị phân.
        """
        logger.info(
            "Reading uploaded ingestion document filename=%s mime_type=%s byte_count=%s",
            filename,
            mime_type,
            len(data),
        )

        # 1. Nạp nội dung từ byte qua loader
        try:
            document = self.loader.load_bytes(
                filename=filename, data=data, mime_type=mime_type
            )
        except (UnicodeError, ValueError) as exc:
            raise DocumentReadError(str(exc)) from exc

        # 2. Phân đoạn tài liệu thành các chunks
        return self._split(document)

    def _split(self, document: LoadedDocument) -> list[DocumentChunk]:
        """
        Tiền xử lý Markdown và chia tài liệu thành danh sách các DocumentChunk.

        Args:
            document: Đối tượng LoadedDocument chứa nội dung thô đã nạp.

        Returns:
            list[DocumentChunk]: Danh sách các đoạn văn bản đã phân chia.

        Raises:
            DocumentReadError: Nếu tài liệu rỗng hoặc không tạo được chunk nào.
        """
        # 1. Kiểm tra tài liệu rỗng
        if not document.text.strip():
            raise DocumentReadError(f"Document is empty: {document.source}")

        # 2. Nếu là file Markdown (.md), chạy qua bộ tiền xử lý Markdown
        if document.suffix == ".md":
            raw_chars = len(document.text)
            preprocess_result = self.preprocessor.preprocess(document.text)
            processed_chars = len(preprocess_result.processed_text)
            logger.info(
                "Document preprocessed source=%s raw_chars=%s processed_chars=%s chars_saved=%s valid=%s",
                document.source,
                raw_chars,
                processed_chars,
                raw_chars - processed_chars,
                preprocess_result.is_valid,
            )
            document = LoadedDocument(
                source=document.source,
                suffix=document.suffix,
                text=preprocess_result.processed_text,
                mime_type=document.mime_type,
            )

        # 3. Phân chia tài liệu thành các chunk theo chiến lược chunker
        chunks = self.chunker.split(document)
        if not chunks:
            raise DocumentReadError(
                f"Document contains no chunkable content: {document.source}"
            )

        # 4. Ghi log tổng kết kết quả chuẩn bị tài liệu
        logger.info(
            "Ingestion document prepared source=%s suffix=%s char_count=%s chunk_count=%s chunker=%s",
            document.source,
            document.suffix,
            len(document.text),
            len(chunks),
            getattr(self.chunker, "version", type(self.chunker).__name__),
        )
        return chunks
