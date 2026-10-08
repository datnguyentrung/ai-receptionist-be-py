"""Các chiến lược nạp và phân tách cấu trúc tài liệu phục vụ quy trình Ingestion.

Module này cung cấp các chiến lược đọc (LoaderStrategy), làm sạch và phân đoạn tài liệu (ChunkingStrategy)
thành các DocumentChunk có định danh ổn định (stable_chunk_id) và cấu trúc phân cấp theo Markdown heading.

Danh sách các hàm / phương thức trong module:
- `_sha256(...)`: Tạo mã băm SHA-256 cho chuỗi văn bản UTF-8.
- `stable_document_id(...)`: Tạo ID định danh ổn định cho tài liệu dựa trên tên tệp nguồn.
- `_is_markdown_table_separator(...)`: Kiểm tra dòng phân cách bảng Markdown.
- `_strip_inline_markdown(...)`: Loại bỏ các định dạng Markdown inline (in đậm, nghiêng, code).
- `canonicalize_markdown_plain_text(...)`: Chuyển Markdown sang plain text chuẩn hóa để trích xuất bằng chứng.
- `chunk_content_hash(...)`: Tính mã băm SHA-256 cho nội dung của một chunk.
- `stable_chunk_id(...)`: Tạo ID định danh ổn định cho chunk dựa trên đường dẫn cấu trúc và hash nội dung.
- `preclean_document_text(...)`: Làm sạch nhẹ văn bản trước khi thực hiện chia chunk.
- `ChunkingStrategy`: Giao diện (Protocol) định nghĩa chiến lược chia nhỏ tài liệu.
- `StructuralTextChunker`: Chiến lược phân tách tài liệu Markdown theo cấu trúc cây tiêu đề heading.
- `Utf8TextLoader`: Bộ nạp và giải mã tệp tin văn bản UTF-8 (.md, .txt).
- `LoaderStrategy`: Giao diện (Protocol) định nghĩa bộ nạp tài liệu nguồn.
"""

import hashlib
import re
from pathlib import Path
from typing import Protocol

from app.schemas.ingestion import DocumentChunk, LoadedDocument


def _sha256(value: str) -> str:
    """
    Tạo mã hash SHA-256 cho chuỗi văn bản UTF-8.

    Args:
        value: Chuỗi văn bản cần băm.

    Returns:
        str: Mã hash SHA-256 dạng hex string.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_document_id(source: str) -> str:
    """
    Tạo ID định danh ổn định (stable ID) cho tài liệu dựa trên tên file nguồn.

    Args:
        source: Tên file nguồn hoặc đường dẫn tệp.

    Returns:
        str: Stable document ID dạng 'doc_<hash>'.
    """
    # 1. Lấy tên file và chuẩn hóa về chữ thường
    normalized = str(Path(source).name).strip().casefold()

    # 2. Hash và trả về ID định dạng doc_...
    return f"doc_{_sha256(normalized)[:32]}"


def _is_markdown_table_separator(value: str) -> bool:
    """
    Kiểm tra xem dòng văn bản có phải là dòng phân cách header/body của bảng Markdown không.

    Args:
        value: Dòng văn bản cần kiểm tra.

    Returns:
        bool: True nếu là dòng phân cách bảng, ngược lại False.
    """
    # 1. Loại bỏ dấu '|' và khoảng trắng
    body = value.strip("|").replace(" ", "")
    # 2. Dòng hợp lệ phải không rỗng và chỉ gồm các ký tự ':' hoặc '-'
    return bool(body) and all(ch in {":", "-"} for ch in body)


def _strip_inline_markdown(value: str) -> str:
    """
    Loại bỏ các định dạng Markdown inline như in đậm, in nghiêng và inline code.

    Args:
        value: Chuỗi văn bản chứa cú pháp Markdown inline.

    Returns:
        str: Chuỗi văn bản đã loại bỏ ký tự định dạng.
    """
    # 1. Bỏ định dạng in đậm **...**
    value = re.sub(r"(?<!\*)\*\*([^*]+)\*\*(?!\*)", r"\1", value)
    # 2. Bỏ định dạng in đậm __...__
    value = re.sub(r"(?<!_)__([^_]+)__(?!_)", r"\1", value)
    # 3. Bỏ định dạng in nghiêng *...*
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", value)
    # 4. Bỏ định dạng in nghiêng _..._
    value = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", value)
    # 5. Bỏ định dạng code inline `...`
    value = re.sub(r"`([^`]+)`", r"\1", value)
    return value


def canonicalize_markdown_plain_text(content: str) -> str:
    """
    Chuyển đổi Markdown sang dạng văn bản thô (plain text) chuẩn hóa để trích xuất bằng chứng.

    Args:
        content: Nội dung văn bản Markdown gốc.

    Returns:
        str: Nội dung plain text chuẩn hóa.
    """
    # 1. Chuẩn hóa tất cả các kiểu ngắt dòng (\r\n, \r) về \n rồi tách thành danh sách các dòng
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    plain: list[str] = []

    for line in lines:
        stripped = line.strip()
        # 2. Xử lý dòng trống: chỉ giữ tối đa 1 dòng trống liên tiếp
        if not stripped:
            if plain and plain[-1] != "":
                plain.append("")
            continue

        # 3. Bỏ qua các dòng phân cách bảng (ví dụ: |---|---|)
        if _is_markdown_table_separator(stripped):
            continue

        # 4. Xử lý hàng trong bảng Markdown
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [
                _strip_inline_markdown(cell.strip())
                for cell in stripped.strip("|").split("|")
            ]
            if len(cells) >= 2:
                plain.append(f"{cells[0]}: {' | '.join(cells[1:])}")
            elif cells:
                plain.append(cells[0])
            continue

        # 5. Với các dòng văn bản thông thường, loại bỏ các ký tự Markdown inline
        plain.append(_strip_inline_markdown(line).strip())

    # 6. Xóa các dòng trống ở cuối danh sách
    while plain and plain[-1] == "":
        plain.pop()

    # 7. Ghép lại thành chuỗi văn bản hoàn chỉnh
    return "\n".join(plain).strip()


def chunk_content_hash(content: str) -> str:
    """
    Tính mã hash SHA-256 cho nội dung của một chunk để kiểm tra thay đổi/trùng lặp.

    Args:
        content: Nội dung chuỗi văn bản của chunk.

    Returns:
        str: Mã băm SHA-256.
    """
    return _sha256(content)


def stable_chunk_id(*, structural_path: str, content_hash: str) -> str:
    """
    Tạo ID định danh ổn định (deterministic chunk ID) cho một chunk.

    Args:
        structural_path: Đường dẫn cấu trúc phân cấp (ví dụ: 'Chương 1 > Mục 1.1#0').
        content_hash: Mã băm nội dung của chunk.

    Returns:
        str: Mã chunk ID định dạng 'chk_<hash>'.
    """
    material = f"{structural_path}\0{content_hash}"
    return f"chk_{_sha256(material)[:40]}"


def preclean_document_text(content: str) -> str:
    """
    Làm sạch nhẹ toàn bộ tài liệu TRƯỚC khi thực hiện chia chunk.

    Args:
        content: Nội dung văn bản thô ban đầu.

    Returns:
        str: Nội dung văn bản đã làm sạch khoảng trắng rác và chuẩn hóa xuống dòng.
    """
    # 1. Loại UTF-8 BOM nếu xuất hiện ở đầu tài liệu
    content = content.removeprefix("\ufeff")

    # 2. Chuẩn hóa newline về \n
    content = content.replace("\r\n", "\n").replace("\r", "\n")

    # 3. Xóa các control character không có ý nghĩa văn bản (giữ lại \n và \t)
    content = "".join(
        char for char in content if char in ("\n", "\t") or ord(char) >= 32
    )

    # 4. Xóa whitespace thừa ở cuối mỗi dòng
    content = "\n".join(line.rstrip() for line in content.split("\n"))

    # 5. Co 3+ dòng trống liên tiếp xuống còn 2 dòng trống
    content = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", content)

    # 6. Xóa dòng trống/whitespace ở đầu và cuối toàn tài liệu
    return content.strip()


class ChunkingStrategy(Protocol):
    """
    Giao diện (Protocol) định nghĩa chiến lược phân tách tài liệu thành danh sách DocumentChunk.
    """

    version: str

    def split(self, document: LoadedDocument) -> list[DocumentChunk]: ...


class StructuralTextChunker:
    """
    Chiến lược phân tách tài liệu Markdown theo cấu trúc cây tiêu đề (headings).
    """

    # Regex nhận diện dòng tiêu đề Markdown từ level 1 đến 6
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

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
        """
        Khởi tạo đối tượng DocumentChunk với đầy đủ metadata và ID định danh cố định.

        Args:
            source: Tên tài liệu nguồn.
            index: Chỉ số thứ tự của chunk.
            section: Tiêu đề section trực tiếp của chunk.
            content: Nội dung văn bản của chunk.
            structural_path: Đường dẫn phân cấp cấu trúc heading.
            start_line: Dòng bắt đầu trong tài liệu gốc.
            end_line: Dòng kết thúc trong tài liệu gốc.

        Returns:
            DocumentChunk: Đối tượng chunk đã khởi tạo.
        """
        # 1. Chuẩn hóa nội dung chunk thành plain text
        canonical_content = canonicalize_markdown_plain_text(content)
        # 2. Tính mã hash nội dung
        content_hash = chunk_content_hash(canonical_content)
        # 3. Tạo đối tượng DocumentChunk hoàn chỉnh
        return DocumentChunk(
            index=index,
            source=source,
            section=section,
            content=canonical_content,
            chunkId=stable_chunk_id(
                structural_path=structural_path,
                content_hash=content_hash,
            ),
            contentHash=content_hash,
            structuralPath=structural_path,
            startLine=start_line,
            endLine=end_line,
        )

    def _split_markdown(self, document: LoadedDocument) -> list[DocumentChunk]:
        """
        Duyệt từng dòng của file Markdown, phân nhóm nội dung theo cây phân cấp heading và tạo chunk.

        Args:
            document: Đối tượng LoadedDocument chứa nội dung Markdown.

        Returns:
            list[DocumentChunk]: Danh sách các chunks theo cấu trúc heading.
        """
        chunks: list[DocumentChunk] = []
        current_section: str | None = None
        current_path = "__preamble__"
        current_lines: list[str] = []
        current_start_line: int | None = None
        heading_stack: list[str] = []
        path_occurrences: dict[str, int] = {}

        def flush() -> None:
            """
            Đóng gói các dòng nội dung hiện tại thành một DocumentChunk và reset bộ đệm.
            """
            nonlocal current_lines, current_start_line
            content = "\n".join(current_lines).strip()
            if not content:
                current_lines = []
                current_start_line = None
                return
            if current_section and not content.startswith(current_section):
                content = f"{current_section}\n\n{content}"
            first_offset = next(
                i for i, line in enumerate(current_lines) if line.strip()
            )
            last_offset = (
                len(current_lines)
                - 1
                - next(
                    i for i, line in enumerate(reversed(current_lines)) if line.strip()
                )
            )
            base_line = current_start_line or 1
            occurrence = path_occurrences.get(current_path, 0)
            path_occurrences[current_path] = occurrence + 1
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
            current_lines = []
            current_start_line = None

        # Duyệt qua từng dòng trong tài liệu kèm chỉ số dòng
        for line_number, line in enumerate(document.text.splitlines(), start=1):
            heading = self.heading_pattern.match(line)
            if heading:
                flush()
                level = len(heading.group(1))
                title = heading.group(2).strip()
                heading_stack = heading_stack[: level - 1]
                heading_stack.append(title)
                current_section = title
                current_path = " > ".join(heading_stack)
                continue
            if current_start_line is None:
                current_start_line = line_number
            current_lines.append(line)

        # Đóng gói phần nội dung còn lại cuối file
        flush()
        return chunks

    def split(self, document: LoadedDocument) -> list[DocumentChunk]:
        """
        Chia tài liệu thành các chunks theo cấu trúc heading (nếu là Markdown) hoặc toàn bộ file.

        Args:
            document: Đối tượng LoadedDocument.

        Returns:
            list[DocumentChunk]: Danh sách các chunk.
        """
        # 1. Nếu là tệp Markdown, chia theo cấu trúc các heading
        if document.suffix == ".md":
            return self._split_markdown(document)
        # 2. Với file không phải Markdown, tạo 1 chunk bao trọn toàn bộ nội dung
        return [
            self._chunk(
                index=0,
                source=document.source,
                section=None,
                content=document.text.strip(),
                structural_path="__document__#0",
                start_line=1,
                end_line=max(1, len(document.text.splitlines())),
            )
        ]


class Utf8TextLoader:
    """
    Bộ nạp tài liệu dạng text thuần/Markdown hỗ trợ mã hóa UTF-8.
    """

    # Danh sách phần mở rộng file được hỗ trợ
    supported_suffixes = frozenset({".md", ".txt"})

    def _validate_suffix(self, source: str) -> str:
        """
        Kiểm tra và trả về phần mở rộng của file, báo lỗi nếu không hỗ trợ.

        Args:
            source: Tên tệp hoặc đường dẫn file.

        Returns:
            str: Phần mở rộng (ví dụ: '.md', '.txt').

        Raises:
            ValueError: Nếu đuôi file không nằm trong danh sách hỗ trợ.
        """
        suffix = Path(source).suffix.lower()
        if suffix not in self.supported_suffixes:
            raise ValueError(f"Unsupported document type: {suffix}")
        return suffix

    def load_path(self, path: Path) -> LoadedDocument:
        """
        Đọc và nạp nội dung tài liệu từ đường dẫn file Path.

        Args:
            path: Đối tượng Path trỏ tới tệp tin.

        Returns:
            LoadedDocument: Đối tượng tài liệu chứa văn bản đã làm sạch.
        """
        suffix = self._validate_suffix(path.name)
        raw_text = path.read_text(encoding="utf-8")

        # Làm sạch nhẹ toàn bộ tài liệu trước khi chunk
        cleaned_text = preclean_document_text(raw_text)

        return LoadedDocument(
            source=path.name,
            suffix=suffix,
            text=cleaned_text,
        )

    def load_bytes(
        self, *, filename: str, data: bytes, mime_type: str | None = None
    ) -> LoadedDocument:
        """
        Giải mã mảng byte thành văn bản UTF-8 và tạo đối tượng LoadedDocument.

        Args:
            filename: Tên tệp tin gốc.
            data: Mảng byte dữ liệu.
            mime_type: Định dạng MIME (tuỳ chọn).

        Returns:
            LoadedDocument: Đối tượng tài liệu chứa văn bản đã làm sạch.

        Raises:
            ValueError: Nếu mảng byte không hợp lệ chuẩn UTF-8.
        """
        suffix = self._validate_suffix(filename)
        try:
            # Giải mã bytes sang chuỗi UTF-8
            raw_text = data.decode("utf-8")

            # Làm sạch nhẹ toàn bộ tài liệu trước khi chunk
            cleaned_text = preclean_document_text(raw_text)
        except UnicodeDecodeError as exc:
            raise ValueError(f"Document is not valid UTF-8: {filename}") from exc
        return LoadedDocument(
            source=filename,
            suffix=suffix,
            text=cleaned_text,
            mime_type=mime_type,
        )


class LoaderStrategy(Protocol):
    """
    Giao diện (Protocol) định nghĩa bộ nạp tài liệu thành văn bản UTF-8 chuẩn hóa.
    """

    supported_suffixes: frozenset[str]

    def load_path(self, path: Path) -> LoadedDocument: ...

    def load_bytes(
        self, *, filename: str, data: bytes, mime_type: str | None = None
    ) -> LoadedDocument: ...
