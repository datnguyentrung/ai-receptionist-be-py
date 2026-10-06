"""Pluggable loading/chunking strategies with deterministic source identity."""

import hashlib
import re
from pathlib import Path
from typing import Protocol

from app.core.schemas.ingestion.document import DocumentChunk, LoadedDocument

# Phiên bản thuật toán chunking theo cấu trúc Markdown
STRUCTURAL_CHUNKER_VERSION = "structural-v4-canonical-plain-text"
# Phiên bản thuật toán sinh ID định danh tài liệu
DOCUMENT_ID_VERSION = "document-id-v1"


def _sha256(value: str) -> str:
    """Hàm băm SHA-256 chuỗi văn bản UTF-8 thành mã hex string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_document_id(source: str) -> str:
    """Tạo ID định danh ổn định (stable ID) cho tài liệu dựa trên tên file.
    
    ID này không đổi ngay cả khi nội dung tài liệu được cập nhật lại.
    """
    # Chuẩn hóa tên file: lấy tên gốc, loại bỏ khoảng trắng và chuyển về chữ thường
    normalized = str(Path(source).name).strip().casefold()
    # Kết hợp version với tên file chuẩn hóa qua ký tự null delimiter
    material = f"{DOCUMENT_ID_VERSION}\0{normalized}"
    # Trả về chuỗi ID bắt đầu bằng "doc_" cùng 32 ký tự đầu mã băm SHA256
    return f"doc_{_sha256(material)[:32]}"


def chunk_content_hash(content: str) -> str:
    """Tính mã hash SHA-256 cho nội dung của một chunk để kiểm tra thay đổi/trùng lặp."""
    return _sha256(content)


def canonicalize_markdown_plain_text(content: str) -> str:
    """Chuyển đổi Markdown sang dạng văn bản thô (plain text) chuẩn hóa để trích xuất bằng chứng (evidence).
    
    - Chuẩn hóa định dạng xuống dòng.
    - Chuyển bảng Markdown thành dạng text 'cột 1: cột 2 | cột 3'.
    - Loại bỏ các ký tự in đậm, nghiêng, code inline.
    """
    # Chuẩn hóa tất cả các kiểu ngắt dòng (\r\n, \r) về \n rồi tách thành danh sách các dòng
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    plain: list[str] = []
    
    for line in lines:
        stripped = line.strip()
        # Nếu là dòng trống
        if not stripped:
            # Chỉ thêm tối đa 1 dòng trống liên tiếp để tránh khoảng trắng thừa
            if plain and plain[-1] != "":
                plain.append("")
            continue
        
        # Bỏ qua các dòng phân cách bảng (ví dụ: |---|---|)
        if _is_markdown_table_separator(stripped):
            continue
            
        # Xử lý hàng trong bảng Markdown: bắt đầu và kết thúc bằng '|'
        if stripped.startswith("|") and stripped.endswith("|"):
            # Tách các cell, bỏ dấu '|' ở 2 đầu và xóa cú pháp Markdown inline trong từng cell
            cells = [_strip_inline_markdown(cell.strip()) for cell in stripped.strip("|").split("|")]
            if len(cells) >= 2:
                # Định dạng hàng bảng thành: "tiêu đề hàng/cột 1: giá trị cột 2 | giá trị cột 3"
                plain.append(f"{cells[0]}: {' | '.join(cells[1:])}")
            elif cells:
                plain.append(cells[0])
            continue
            
        # Với các dòng văn bản thông thường, loại bỏ các ký tự Markdown inline
        plain.append(_strip_inline_markdown(line).strip())
        
    # Xóa các dòng trống ở cuối danh sách
    while plain and plain[-1] == "":
        plain.pop()
        
    # Ghép lại thành chuỗi văn bản hoàn chỉnh
    return "\n".join(plain).strip()


def _is_markdown_table_separator(value: str) -> bool:
    """Kiểm tra xem dòng văn bản có phải là dòng phân cách header/body của bảng Markdown không (ví dụ: |---|:---:|)."""
    # Loại bỏ dấu '|' và khoảng trắng
    body = value.strip("|").replace(" ", "")
    # Dòng hợp lệ phải không rỗng và chỉ gồm các ký tự ':' hoặc '-'
    return bool(body) and all(ch in {":", "-"} for ch in body)


def _strip_inline_markdown(value: str) -> str:
    """Loại bỏ các định dạng Markdown inline như in đậm (**text**, __text__), in nghiêng (*text*, _text_), và inline code (`text`)."""
    # Bỏ định dạng in đậm **...**
    value = re.sub(r"(?<!\*)\*\*([^*]+)\*\*(?!\*)", r"\1", value)
    # Bỏ định dạng in đậm __...__
    value = re.sub(r"(?<!_)__([^_]+)__(?!_)", r"\1", value)
    # Bỏ định dạng in nghiêng *...*
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", value)
    # Bỏ định dạng in nghiêng _..._
    value = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", value)
    # Bỏ định dạng code inline `...`
    value = re.sub(r"`([^`]+)`", r"\1", value)
    return value


def stable_chunk_id(
    *, document_id: str, structural_path: str, content_hash: str
) -> str:
    """Tạo ID định danh ổn định (deterministic chunk ID) cho một chunk.
    
    ID phụ thuộc vào: ID tài liệu, đường dẫn cấu trúc (section path) và hash nội dung.
    """
    material = f"{document_id}\0{structural_path}\0{content_hash}"
    return f"chk_{_sha256(material)[:40]}"


class LoaderStrategy(Protocol):
    """Giao diện (Protocol) định nghĩa bộ nạp tài liệu thành văn bản UTF-8 chuẩn hóa."""

    supported_suffixes: frozenset[str]

    def load_path(self, path: Path) -> LoadedDocument: ...

    def load_bytes(
        self, *, filename: str, data: bytes, mime_type: str | None = None
    ) -> LoadedDocument: ...


class Utf8TextLoader:
    """Bộ nạp tài liệu dạng text thuần/Markdown hỗ trợ mã hóa UTF-8."""
    # Danh sách phần mở rộng file được hỗ trợ
    supported_suffixes = frozenset({".md", ".txt"})

    def _validate_suffix(self, source: str) -> str:
        """Kiểm tra và trả về phần mở rộng của file, báo lỗi nếu không hỗ trợ."""
        suffix = Path(source).suffix.lower()
        if suffix not in self.supported_suffixes:
            raise ValueError(f"Unsupported document type: {suffix}")
        return suffix

    def load_path(self, path: Path) -> LoadedDocument:
        """Đọc và nạp nội dung tài liệu từ đường dẫn file Path."""
        suffix = self._validate_suffix(path.name)
        return LoadedDocument(
            source=path.name,
            suffix=suffix,
            text=path.read_text(encoding="utf-8"),
        )

    def load_bytes(
        self, *, filename: str, data: bytes, mime_type: str | None = None
    ) -> LoadedDocument:
        """Giải mã mảng byte thành văn bản UTF-8 và tạo đối tượng LoadedDocument."""
        suffix = self._validate_suffix(filename)
        try:
            # Giải mã bytes sang chuỗi UTF-8
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Document is not valid UTF-8: {filename}") from exc
        return LoadedDocument(
            source=filename,
            suffix=suffix,
            text=text,
            mime_type=mime_type,
        )


class ChunkingStrategy(Protocol):
    """Giao diện (Protocol) định nghĩa chiến lược phân tách tài liệu thành danh sách DocumentChunk."""
    version: str

    def split(self, document: LoadedDocument) -> list[DocumentChunk]: ...


class StructuralTextChunker:
    """Chiến lược chunking dựa trên cấu trúc phân cấp Heading của Markdown và gán định danh cố định."""

    version = STRUCTURAL_CHUNKER_VERSION
    # Regex nhận diện dòng tiêu đề Markdown từ level 1 đến 6 (ví dụ: # Tiêu đề, ## Mục con)
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

    def split(self, document: LoadedDocument) -> list[DocumentChunk]:
        """Chia tài liệu thành các chunks:
        
        - Nếu là file Markdown (.md): Chia theo cấu trúc các heading.
        - Nếu là file khác: Giữ nguyên toàn bộ nội dung thành 1 chunk duy nhất.
        """
        if document.suffix == ".md":
            return self._split_markdown(document)
        # Đối với các file không phải Markdown, tạo 1 chunk bao trọn toàn bộ file
        return [
            self._chunk(
                source=document.source,
                index=0,
                section=None,
                content=document.text.strip(),
                structural_path="__document__#0",
                start_line=1,
                end_line=max(1, len(document.text.splitlines())),
            )
        ]

    @staticmethod
    def _chunk(
        *,
        source: str,
        index: int,
        section: str | None,
        content: str,
        structural_path: str,
        start_line: int,
        end_line: int,
    ) -> DocumentChunk:
        """Hàm khởi tạo đối tượng DocumentChunk với đầy đủ metadata và ID định danh cố định."""
        # Lấy ID tài liệu cố định theo tên nguồn
        document_id = stable_document_id(source)
        # Chuẩn hóa nội dung chunk thành plain text
        canonical_content = canonicalize_markdown_plain_text(content)
        # Tính mã băm nội dung
        content_hash = chunk_content_hash(canonical_content)
        return DocumentChunk(
            index=index,
            source=source,
            section=section,
            content=canonical_content,
            documentId=document_id,
            chunkId=stable_chunk_id(
                document_id=document_id,
                structural_path=structural_path,
                content_hash=content_hash,
            ),
            contentHash=content_hash,
            structuralPath=structural_path,
            startLine=start_line,
            endLine=end_line,
        )

    def _split_markdown(self, document: LoadedDocument) -> list[DocumentChunk]:
        """Duyệt từng dòng của file Markdown, phân nhóm nội dung theo cây phân cấp heading và tạo chunk."""
        chunks: list[DocumentChunk] = []
        current_section: str | None = None  # Tên section hiện tại
        current_path = "__preamble__"       # Đường dẫn cấu trúc phân cấp (mặc định là phần mở đầu)
        current_lines: list[str] = []       # Các dòng nội dung đang gom cho chunk hiện tại
        current_start_line: int | None = None  # Dòng bắt đầu của chunk
        heading_stack: list[str] = []       # Stack lưu trữ cây phả hệ tiêu đề (vd: ['Chương 1', 'Mục 1.1'])
        path_occurrences: dict[str, int] = {}  # Đếm số lần xuất hiện của cùng một structural_path

        def flush() -> None:
            """Đóng gói các dòng nội dung hiện tại thành một DocumentChunk và reset bộ đệm."""
            nonlocal current_lines, current_start_line
            content = "\n".join(current_lines).strip()
            # Nếu nội dung rỗng (chỉ có khoảng trắng), bỏ qua
            if not content:
                current_lines = []
                current_start_line = None
                return
            # Gắn tên section vào đầu nội dung nếu nội dung chưa chứa section đó
            if current_section and not content.startswith(current_section):
                content = f"{current_section}\n\n{content}"
            # Tìm offset dòng thực tế đầu tiên có chữ
            first_offset = next(
                i for i, line in enumerate(current_lines) if line.strip()
            )
            # Tìm offset dòng cuối cùng có chữ
            last_offset = (
                len(current_lines)
                - 1
                - next(
                    i for i, line in enumerate(reversed(current_lines)) if line.strip()
                )
            )
            base_line = current_start_line or 1
            # Lấy số thứ tự xuất hiện của structural_path này
            occurrence = path_occurrences.get(current_path, 0)
            path_occurrences[current_path] = occurrence + 1
            # Tạo chunk và thêm vào danh sách kết quả
            chunks.append(
                self._chunk(
                    source=document.source,
                    index=len(chunks),
                    section=current_section,
                    content=content,
                    structural_path=f"{current_path}#{occurrence}",
                    start_line=base_line + first_offset,
                    end_line=base_line + last_offset,
                )
            )
            # Reset bộ đệm các dòng
            current_lines = []
            current_start_line = None

        # Duyệt qua từng dòng trong tài liệu kèm chỉ số dòng (bắt đầu từ dòng 1)
        for line_number, line in enumerate(document.text.splitlines(), start=1):
            heading = self.heading_pattern.match(line)
            if heading:
                # Gặp tiêu đề mới -> đóng gói nội dung của section trước đó
                flush()
                level = len(heading.group(1))   # Cấp độ heading (# -> 1, ## -> 2, ...)
                title = heading.group(2).strip() # Tiêu đề heading
                # Cắt ngắn heading_stack theo cấp độ hiện tại để duy trì quan hệ cha-con
                heading_stack = heading_stack[: level - 1]
                heading_stack.append(title)
                current_section = title
                # Tạo đường dẫn breadcrumb phân cấp: "Cha > Con > Cháu"
                current_path = " > ".join(heading_stack)
                continue
            # Ghi nhận dòng bắt đầu nếu đang ở đầu đoạn mới
            if current_start_line is None:
                current_start_line = line_number
            current_lines.append(line)
            
        # Đóng gói phần nội dung còn lại cuối file
        flush()
        return chunks


__all__ = [
    "DOCUMENT_ID_VERSION",
    "STRUCTURAL_CHUNKER_VERSION",
    "ChunkingStrategy",
    "LoadedDocument",
    "LoaderStrategy",
    "StructuralTextChunker",
    "Utf8TextLoader",
    "canonicalize_markdown_plain_text",
    "chunk_content_hash",
    "stable_chunk_id",
    "stable_document_id",
]
