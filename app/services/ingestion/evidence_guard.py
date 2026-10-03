"""Canonicalize evidence excerpts against prepared chunk text before validation."""

from typing import Any

import logging
import re
from collections.abc import Callable

from app.schemas.ingestion_schema import Evidence, GraphPatchFragment, PreparedChunk

logger = logging.getLogger(__name__)


def _chunk_surfaces(chunk: PreparedChunk) -> list[str]:
    surfaces = [chunk.text]
    if chunk.section:
        surfaces.extend([
            f"{chunk.section}\n\n{chunk.text}",
            f"{chunk.section}\n{chunk.text}",
            chunk.section,
        ])
    return surfaces


def canonical_whitespace_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """Return the chunk surface when the quote only differs by whitespace."""
    if not quote.strip():
        return quote
    parts = [re.escape(part) for part in re.split(r"\s+", quote.strip()) if part]
    if not parts:
        return quote
    pattern = r"\s+".join(parts)
    for surface in _chunk_surfaces(chunk):
        match = re.search(pattern, surface, flags=re.MULTILINE)
        if match is not None:
            return match.group(0)
    return quote


def canonical_markdown_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """Return a chunk line/paragraph when markdown markers are the only mismatch."""
    for surface in _chunk_surfaces(chunk):
        if quote in surface:
            return quote

    def plain(value: str) -> str:
        value = re.sub(r"(\*\*|__|`|\*)", "", value)
        return re.sub(r"\s+", " ", value).strip()

    target = plain(quote)
    if not target:
        return quote
    for surface in _chunk_surfaces(chunk):
        for line in surface.splitlines():
            if target in plain(line):
                return line.strip()
        for paragraph in _paragraphs(surface):
            if target in plain(paragraph):
                return paragraph
    return quote


def canonical_table_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """Return a table-like field line from canonical plain text."""
    normalized = quote.replace("\r\n", "\n").replace("\r", "\n").strip()
    if ":" not in normalized and "|" not in normalized:
        return quote
    target = _plain_line(normalized)
    for surface in _chunk_surfaces(chunk):
        for line in surface.splitlines():
            candidate = line.strip()
            if target and target in _plain_line(candidate):
                return candidate
    return quote


def canonical_bullet_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """Return the matching bullet/list line from the chunk when available."""
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
    """Complete a truncated quote when its normalized prefix is unique in the chunk."""
    if not quote.strip():
        return quote
    for surface in _chunk_surfaces(chunk):
        if quote in surface and not _looks_truncated_prefix(surface, quote):
            return quote
        normalized_chunk = _collapse_space(surface)
        normalized_quote = _collapse_space(quote)
        if not normalized_quote:
            continue
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
    """Locate the exact verbatim sentence/line containing the quote or property value."""
    target = quote.strip() if quote.strip() else (str(fallback_value).strip() if fallback_value is not None else "")
    if not target:
        return quote
    for surface in _chunk_surfaces(chunk):
        if target in surface:
            for line in surface.splitlines():
                if target in line:
                    return line.strip()
            for para in _paragraphs(surface):
                if target in para:
                    return para.strip()
        # Case-insensitive / whitespace-insensitive match
        norm_target = _collapse_space(target).casefold()
        for line in surface.splitlines():
            if norm_target in _collapse_space(line).casefold():
                return line.strip()
    return quote


class GraphFragmentEvidenceGuard:
    """Deep module that fixes only evidence surfaces; graph semantics stay unchanged."""

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
        chunk_by_index = {chunk.chunk_index: chunk for chunk in chunks}

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
                
                # If text is empty, seed with fallback_value
                if not text.strip() and fallback_value is not None:
                    text = canonical_sentence_window_excerpt(chunk, "", fallback_value)

                for normalizer in self._normalizers:
                    candidate = (
                        normalizer(chunk, text, fallback_value)
                        if normalizer is canonical_sentence_window_excerpt
                        else normalizer(chunk, text)
                    )
                    if candidate != text:
                        text = candidate
                    if any(text in s and not _looks_truncated_prefix(s, text) for s in surfaces):
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
        return fragment.model_copy(update={"nodes": nodes, "edges": edges})


def _collapse_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _plain_line(value: str) -> str:
    value = re.sub(r"[*_`|]+", "", value)
    return _collapse_space(value)


def _paragraphs(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", value) if part.strip()]


def _sentence_end(value: str, start: int) -> int:
    match = re.search(r"(?<=[.!?。！？])\s+|$", value[start:])
    return len(value) if match is None else start + match.start()


def _surface_for_collapsed(surface: str, collapsed_excerpt: str) -> str | None:
    parts = [re.escape(part) for part in collapsed_excerpt.split() if part]
    if not parts:
        return None
    match = re.search(r"\s+".join(parts), surface, flags=re.MULTILINE)
    return match.group(0) if match else None


def _looks_truncated_prefix(surface: str, quote: str) -> bool:
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
