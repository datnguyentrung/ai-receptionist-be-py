"""Chuẩn hóa và làm sạch các trích dẫn bằng chứng (evidence) đối chiếu với nội dung chunk nguồn trước khi xác thực.

Module này cung cấp các cơ chế làm sạch trích dẫn văn bản để đảm bảo evidence khớp chính xác với chunk gốc:
- Bỏ qua sai lệch khoảng trắng (whitespace) và định dạng markdown (bold, italic, code).
- Khôi phục định dạng dòng trong bảng hoặc danh sách gạch đầu dòng (bullet points).
- Tự động hoàn thiện trích dẫn bị cắt cụt (truncated prefix completion).
- Tìm kiếm cửa sổ câu chứa giá trị thuộc tính để làm bằng chứng (sentence window fallback).

Danh sách các hàm / phương thức trong module:
- `_chunk_surfaces(...)`: Trích xuất các biến thể bề mặt văn bản của một chunk (kèm section heading).
- `canonical_whitespace_excerpt(...)`: Khôi phục trích dẫn khi chỉ khác biệt về khoảng trắng.
- `canonical_markdown_excerpt(...)`: Khôi phục trích dẫn khi chỉ khác biệt về ký tự định dạng Markdown.
- `canonical_table_excerpt(...)`: Khôi phục trích dẫn từ các dòng định dạng bảng plain text.
- `canonical_bullet_excerpt(...)`: Khôi phục trích dẫn từ danh sách gạch đầu dòng.
- `canonical_prefix_completion_excerpt(...)`: Hoàn thiện trích dẫn bị cắt ngắn nếu tiền tố là duy nhất.
- `canonical_sentence_window_excerpt(...)`: Tìm chính xác câu hoặc dòng nguyên văn chứa trích dẫn/thuộc tính.
- `GraphFragmentEvidenceGuard`: Lớp điều phối chuẩn hóa toàn bộ trích dẫn trong GraphPatchFragment.
- `GraphFragmentEvidenceGuard.canonicalize(...)`: Chuẩn hóa evidence cho tất cả các nút (nodes), thuộc tính và cạnh (edges).
- `_collapse_space(...)`: Rút gọn khoảng trắng thừa trong chuỗi thành khoảng trắng đơn.
- `_plain_line(...)`: Xóa toàn bộ ký tự Markdown và khoảng trắng thừa trong dòng.
- `_paragraphs(...)`: Tách văn bản thành danh sách các đoạn văn bản.
- `_sentence_end(...)`: Tìm vị trí kết thúc câu dựa trên dấu chấm câu.
- `_surface_for_collapsed(...)`: Khôi phục định dạng văn bản gốc ban đầu từ chuỗi đã rút gọn khoảng trắng.
- `_looks_truncated_prefix(...)`: Kiểm tra xem trích dẫn có đang bị cắt cụt giữa chừng một từ hay không.
"""

import logging
import re
from collections.abc import Callable
from typing import Any

from app.schemas import Evidence, GraphPatchFragment, PreparedChunk

logger = logging.getLogger(__name__)


def _chunk_surfaces(chunk: PreparedChunk) -> list[str]:
    """
    Tạo danh sách các biến thể bề mặt văn bản có thể có của chunk (bao gồm cả tiêu đề section).

    Args:
        chunk: Đối tượng chunk văn bản nguồn.

    Returns:
        list[str]: Danh sách các bề mặt văn bản để so khớp evidence.
    """
    # 1. Bề mặt cơ bản là nội dung text của chunk
    surfaces = [chunk.text]
    # 2. Nếu chunk có section, bổ sung các biến thể ghép tiêu đề section vào trước nội dung
    if chunk.section:
        surfaces.extend(
            [
                f"{chunk.section}\n\n{chunk.text}",
                f"{chunk.section}\n{chunk.text}",
                chunk.section,
            ]
        )
    return surfaces


def canonical_whitespace_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """
    Tìm và trả về chuỗi văn bản nguyên bản trong chunk khi trích dẫn chỉ sai lệch về khoảng trắng.

    Args:
        chunk: Chunk văn bản nguồn.
        quote: Chuỗi trích dẫn cần chuẩn hóa.

    Returns:
        str: Chuỗi văn bản khớp chính xác từ chunk hoặc chuỗi ban đầu nếu không tìm thấy.
    """
    # 1. Nếu trích dẫn rỗng, giữ nguyên
    if not quote.strip():
        return quote

    # 2. Tách trích dẫn thành các từ và escape ký tự regex đặc biệt
    parts = [re.escape(part) for part in re.split(r"\s+", quote.strip()) if part]
    if not parts:
        return quote

    # 3. Tạo mẫu regex cho phép khoảng trắng linh hoạt giữa các từ
    pattern = r"\s+".join(parts)
    for surface in _chunk_surfaces(chunk):
        match = re.search(pattern, surface, flags=re.MULTILINE)
        if match is not None:
            return match.group(0)

    # 4. Trả về trích dẫn gốc nếu không khớp
    return quote


def canonical_markdown_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """
    Tìm dòng hoặc đoạn văn bản trong chunk khi sự không khớp chỉ do các ký tự Markdown (bold, italic, code).

    Args:
        chunk: Chunk văn bản nguồn.
        quote: Chuỗi trích dẫn.

    Returns:
        str: Đoạn văn bản nguyên bản từ chunk.
    """
    # 1. Kiểm tra nếu trích dẫn đã có sẵn nguyên văn trong bề mặt chunk
    for surface in _chunk_surfaces(chunk):
        if quote in surface:
            return quote

    # 2. Hàm loại bỏ ký tự Markdown và khoảng trắng thừa
    def plain(value: str) -> str:
        value = re.sub(r"(\*\*|__|`|\*)", "", value)
        return re.sub(r"\s+", " ", value).strip()

    target = plain(quote)
    if not target:
        return quote

    # 3. So khớp với từng dòng hoặc từng đoạn văn trong bề mặt chunk
    for surface in _chunk_surfaces(chunk):
        for line in surface.splitlines():
            if target in plain(line):
                return line.strip()
        for paragraph in _paragraphs(surface):
            if target in plain(paragraph):
                return paragraph
    return quote


def canonical_table_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """
    Khôi phục dòng dữ liệu dạng bảng từ văn bản plain text chuẩn hóa.

    Args:
        chunk: Chunk văn bản nguồn.
        quote: Chuỗi trích dẫn.

    Returns:
        str: Dòng văn bản khớp từ chunk.
    """
    # 1. Chuẩn hóa ký tự xuống dòng
    normalized = quote.replace("\r\n", "\n").replace("\r", "\n").strip()
    if ":" not in normalized and "|" not in normalized:
        return quote

    # 2. Tìm kiếm dòng tương ứng trong bề mặt chunk
    target = _plain_line(normalized)
    for surface in _chunk_surfaces(chunk):
        for line in surface.splitlines():
            candidate = line.strip()
            if target and target in _plain_line(candidate):
                return candidate
    return quote


def canonical_bullet_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """
    Tìm dòng danh sách gạch đầu dòng (bullet list) tương ứng trong chunk.

    Args:
        chunk: Chunk văn bản nguồn.
        quote: Chuỗi trích dẫn.

    Returns:
        str: Dòng danh sách khớp trong chunk.
    """
    # 1. Duyệt qua các bề mặt văn bản của chunk
    for surface in _chunk_surfaces(chunk):
        if quote in surface:
            return quote
        for line in reversed(quote.splitlines()):
            candidate = line.strip()
            if candidate.startswith(("- ", "* ")) and candidate in surface:
                return candidate
        plain_candidate = quote.strip().lstrip("-* ").strip()
        for line in surface.splitlines():
            stripped = line.strip()
            if stripped.startswith(("- ", "* ")) and plain_candidate in stripped:
                return stripped
    return quote


def canonical_prefix_completion_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """
    Tự động hoàn thiện trích dẫn bị cắt ngắn nếu tiền tố đã chuẩn hóa là duy nhất trong chunk.

    Args:
        chunk: Chunk văn bản nguồn.
        quote: Chuỗi trích dẫn bị cắt ngắn.

    Returns:
        str: Câu trích dẫn hoàn chỉnh.
    """
    # 1. Bỏ qua nếu trích dẫn rỗng
    if not quote.strip():
        return quote

    for surface in _chunk_surfaces(chunk):
        # 2. Nếu đã có trong surface và không bị cắt cụt từ thì giữ nguyên
        if quote in surface and not _looks_truncated_prefix(surface, quote):
            return quote
        normalized_chunk = _collapse_space(surface)
        normalized_quote = _collapse_space(quote)
        if not normalized_quote:
            continue
        # 3. Tìm vị trí xuất hiện duy nhất của chuỗi trong chunk
        start = normalized_chunk.find(normalized_quote)
        if start >= 0 and normalized_chunk.find(normalized_quote, start + 1) < 0:
            end = _sentence_end(normalized_chunk, start + len(normalized_quote))
            completed = normalized_chunk[start:end].strip()
            res = _surface_for_collapsed(surface, completed) or completed
            if res:
                return res
    return quote


def canonical_sentence_window_excerpt(
    chunk: PreparedChunk, quote: str, fallback_value: Any = None
) -> str:
    """
    Định vị chính xác câu hoặc dòng văn bản nguyên văn chứa trích dẫn hoặc giá trị thuộc tính.

    Args:
        chunk: Chunk văn bản nguồn.
        quote: Chuỗi trích dẫn ban đầu.
        fallback_value: Giá trị thay thế (ví dụ giá trị thuộc tính) nếu quote rỗng.

    Returns:
        str: Câu hoặc dòng văn bản tìm thấy trong chunk.
    """
    # 1. Xác định chuỗi mục tiêu cần tìm kiếm
    target = (
        quote.strip()
        if quote.strip()
        else (str(fallback_value).strip() if fallback_value is not None else "")
    )
    if not target:
        return quote

    # 2. Tìm kiếm trong từng dòng hoặc đoạn văn của bề mặt chunk
    for surface in _chunk_surfaces(chunk):
        if target in surface:
            for line in surface.splitlines():
                if target in line:
                    return line.strip()
            for para in _paragraphs(surface):
                if target in para:
                    return para.strip()
        # 3. Tìm kiếm không phân biệt hoa thường và khoảng trắng
        norm_target = _collapse_space(target).casefold()
        for line in surface.splitlines():
            if norm_target in _collapse_space(line).casefold():
                return line.strip()
    return quote


class GraphFragmentEvidenceGuard:
    """
    Module xử lý sâu giúp sửa lỗi bề mặt văn bản của bằng chứng mà không làm thay đổi ngữ nghĩa đồ thị.
    """

    # Danh sách các hàm chuẩn hóa trích dẫn theo thứ tự ưu tiên
    _normalizers: tuple[Callable[[PreparedChunk, str], str], ...] = (
        canonical_prefix_completion_excerpt,
        canonical_table_excerpt,
        canonical_whitespace_excerpt,
        canonical_markdown_excerpt,
        canonical_bullet_excerpt,
        canonical_sentence_window_excerpt,
    )

    def canonicalize(
        self,
        fragment: GraphPatchFragment,
        chunks: list[PreparedChunk],
    ) -> GraphPatchFragment:
        """
        Chuẩn hóa toàn bộ trích dẫn bằng chứng của các nút (nodes), thuộc tính và cạnh (edges) trong mảnh đồ thị.

        Args:
            fragment: Mảnh đồ thị cần chuẩn hóa evidence.
            chunks: Danh sách các chunk văn bản nguồn trong batch.

        Returns:
            GraphPatchFragment: Bản sao mảnh đồ thị với các trích dẫn bằng chứng đã được làm sạch.
        """
        # 1. Tạo bản đồ tra cứu chunk theo index
        chunk_by_index = {chunk.chunk_index: chunk for chunk in chunks}

        # 2. Hàm nội bộ chuẩn hóa danh sách Evidence
        def normalize(
            items: list[Evidence], location: str, fallback_value: Any = None
        ) -> list[Evidence]:
            normalized_items: list[Evidence] = []
            for index, item in enumerate(items):
                chunk = chunk_by_index.get(item.chunk_index)
                if chunk is None:
                    normalized_items.append(item)
                    continue
                text = item.text
                surfaces = _chunk_surfaces(chunk)

                # Nếu text rỗng, sử dụng fallback_value để tìm câu chứa giá trị đó
                if not text.strip() and fallback_value is not None:
                    text = canonical_sentence_window_excerpt(chunk, "", fallback_value)

                # Áp dụng lần lượt các bộ chuẩn hóa
                for normalizer in self._normalizers:
                    candidate = (
                        canonical_sentence_window_excerpt(chunk, text, fallback_value)
                        if normalizer is canonical_sentence_window_excerpt
                        else normalizer(chunk, text)
                    )
                    if candidate != text:
                        text = candidate
                    if any(
                        text in s and not _looks_truncated_prefix(s, text)
                        for s in surfaces
                    ):
                        break

                update = {
                    "source": chunk.source_anchor,
                    "section": chunk.section,
                    "text": text,
                }
                if update["text"] != item.text or update["source"] != item.source:
                    logger.info(
                        "EVIDENCE_CANONICALIZED location=%s chunkIndex=%s old=%r new=%r",
                        f"{location}.{index}",
                        item.chunk_index,
                        item.text[:160],
                        text[:160],
                    )
                normalized_items.append(item.model_copy(update=update))
            return normalized_items

        # 3. Chuẩn hóa evidence cho từng node và từng thuộc tính của node
        nodes = []
        for node_index, node in enumerate(fragment.nodes):
            properties = [
                prop.model_copy(
                    update={
                        "evidence": normalize(
                            prop.evidence,
                            f"nodes.{node_index}.properties.{property_index}.evidence",
                            fallback_value=prop.value,
                        )
                    }
                )
                for property_index, prop in enumerate(node.properties)
            ]
            nodes.append(
                node.model_copy(
                    update={
                        "properties": properties,
                        "evidence": normalize(
                            node.evidence,
                            f"nodes.{node_index}.evidence",
                            fallback_value=node.class_name,
                        ),
                    }
                )
            )

        # 4. Chuẩn hóa evidence cho từng cạnh (edges)
        edges = [
            edge.model_copy(
                update={
                    "evidence": normalize(
                        edge.evidence,
                        f"edges.{edge_index}.evidence",
                        fallback_value=edge.edge_name,
                    )
                }
            )
            for edge_index, edge in enumerate(fragment.edges)
        ]

        # 5. Trả về bản sao fragment đã được chuẩn hóa
        return fragment.model_copy(update={"nodes": nodes, "edges": edges})


def _collapse_space(value: str) -> str:
    """
    Rút gọn tất cả khoảng trắng liên tiếp trong chuỗi thành một dấu cách duy nhất.

    Args:
        value: Chuỗi văn bản đầu vào.

    Returns:
        str: Chuỗi văn bản đã rút gọn khoảng trắng.
    """
    return re.sub(r"\s+", " ", value).strip()


def _plain_line(value: str) -> str:
    """
    Xóa tất cả ký tự Markdown và rút gọn khoảng trắng trong dòng văn bản.

    Args:
        value: Chuỗi văn bản có định dạng Markdown.

    Returns:
        str: Chuỗi văn bản thuần túy.
    """
    value = re.sub(r"[*_`|]+", "", value)
    return _collapse_space(value)


def _paragraphs(value: str) -> list[str]:
    """
    Tách chuỗi văn bản thành danh sách các đoạn văn bản phân cách bởi 2 dấu xuống dòng trở lên.

    Args:
        value: Chuỗi văn bản đầy đủ.

    Returns:
        list[str]: Danh sách các đoạn văn bản không rỗng.
    """
    return [part.strip() for part in re.split(r"\n\s*\n", value) if part.strip()]


def _sentence_end(value: str, start: int) -> int:
    """
    Tìm vị trí kết thúc câu (dấu ., !, ?, ...) bắt đầu từ vị trí start.

    Args:
        value: Chuỗi văn bản.
        start: Vị trí bắt đầu tìm kiếm.

    Returns:
        int: Vị trí kết thúc của câu.
    """
    match = re.search(r"(?<=[.!?。！？])\s+|$", value[start:])
    return len(value) if match is None else start + match.start()


def _surface_for_collapsed(surface: str, collapsed_excerpt: str) -> str | None:
    """
    Tìm lại chuỗi văn bản gốc trong surface từ trích đoạn đã bị rút gọn khoảng trắng.

    Args:
        surface: Bề mặt văn bản gốc của chunk.
        collapsed_excerpt: Trích đoạn đã bị gộp khoảng trắng.

    Returns:
        str | None: Chuỗi văn bản gốc khớp từ surface hoặc None.
    """
    parts = [re.escape(part) for part in collapsed_excerpt.split() if part]
    if not parts:
        return None
    match = re.search(r"\s+".join(parts), surface, flags=re.MULTILINE)
    return match.group(0) if match else None


def _looks_truncated_prefix(surface: str, quote: str) -> bool:
    """
    Kiểm tra xem chuỗi trích dẫn có bị cắt cụt giữa chừng một từ trong surface hay không.

    Args:
        surface: Bề mặt văn bản gốc.
        quote: Chuỗi trích dẫn.

    Returns:
        bool: True nếu ký tự liền sau trích dẫn là chữ cái/chữ số (bị cắt giữa từ), ngược lại False.
    """
    start = surface.find(quote)
    if start < 0:
        return False
    end = start + len(quote)
    if end >= len(surface):
        return False
    previous = quote[-1]
    next_char = surface[end]
    return previous.isalnum() and next_char.isalnum()


__all__ = [
    "GraphFragmentEvidenceGuard",
    "canonical_bullet_excerpt",
    "canonical_markdown_excerpt",
    "canonical_prefix_completion_excerpt",
    "canonical_table_excerpt",
    "canonical_whitespace_excerpt",
]
