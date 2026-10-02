"""Canonicalize evidence excerpts against prepared chunk text before validation."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from app.schemas.ingestion_schema import Evidence, GraphPatchFragment, PreparedChunk

logger = logging.getLogger(__name__)


def canonical_whitespace_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """Return the chunk surface when the quote only differs by whitespace."""
    if not quote.strip():
        return quote
    parts = [re.escape(part) for part in re.split(r"\s+", quote.strip()) if part]
    if not parts:
        return quote
    pattern = r"\s+".join(parts)
    for surface in (chunk.section or "", chunk.text):
        match = re.search(pattern, surface, flags=re.MULTILINE)
        if match is not None:
            return match.group(0)
    return quote


def canonical_markdown_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """Return a chunk line/paragraph when markdown markers are the only mismatch."""
    if quote in chunk.text or quote in (chunk.section or ""):
        return quote

    def plain(value: str) -> str:
        value = re.sub(r"(\*\*|__|`|\*)", "", value)
        return re.sub(r"\s+", " ", value).strip()

    target = plain(quote)
    if not target:
        return quote
    for line in chunk.text.splitlines():
        if target in plain(line):
            return line.strip()
    for paragraph in _paragraphs(chunk.text):
        if target in plain(paragraph):
            return paragraph
    return quote


def canonical_table_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """Return a table-like field line from canonical plain text."""
    normalized = quote.replace("\r\n", "\n").replace("\r", "\n").strip()
    if ":" not in normalized and "|" not in normalized:
        return quote
    target = _plain_line(normalized)
    for line in chunk.text.splitlines():
        candidate = line.strip()
        if target and target in _plain_line(candidate):
            return candidate
    return quote


def canonical_bullet_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """Return the matching bullet/list line from the chunk when available."""
    if quote in chunk.text or quote in (chunk.section or ""):
        return quote
    for line in reversed(quote.splitlines()):
        candidate = line.strip()
        if candidate.startswith(("- ", "* ")) and candidate in chunk.text:
            return candidate
    plain_candidate = quote.strip().lstrip("-* ").strip()
    for line in chunk.text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")) and plain_candidate in stripped:
            return stripped
    return quote


def canonical_prefix_completion_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """Complete a truncated quote when its normalized prefix is unique in the chunk."""
    if not quote.strip():
        return quote
    if quote in chunk.text and not _looks_truncated_prefix(chunk.text, quote):
        return quote
    normalized_chunk = _collapse_space(chunk.text)
    normalized_quote = _collapse_space(quote)
    if not normalized_quote:
        return quote
    start = normalized_chunk.find(normalized_quote)
    if start < 0 or normalized_chunk.find(normalized_quote, start + 1) >= 0:
        return quote
    end = _sentence_end(normalized_chunk, start + len(normalized_quote))
    completed = normalized_chunk[start:end].strip()
    return _surface_for_collapsed(chunk.text, completed) or completed or quote


class GraphFragmentEvidenceGuard:
    """Deep module that fixes only evidence surfaces; graph semantics stay unchanged."""

    _normalizers: tuple[Callable[[PreparedChunk, str], str], ...] = (
        canonical_prefix_completion_excerpt,
        canonical_table_excerpt,
        canonical_whitespace_excerpt,
        canonical_markdown_excerpt,
        canonical_bullet_excerpt,
    )

    def canonicalize(
        self,
        fragment: GraphPatchFragment,
        chunks: list[PreparedChunk],
    ) -> GraphPatchFragment:
        chunk_by_index = {chunk.chunk_index: chunk for chunk in chunks}

        def normalize(items: list[Evidence], location: str) -> list[Evidence]:
            normalized_items: list[Evidence] = []
            for index, item in enumerate(items):
                chunk = chunk_by_index.get(item.chunk_index)
                if chunk is None:
                    normalized_items.append(item)
                    continue
                text = item.text
                for normalizer in self._normalizers:
                    candidate = normalizer(chunk, text)
                    if candidate != text:
                        text = candidate
                    if text in chunk.text and not _looks_truncated_prefix(chunk.text, text):
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
                            node.evidence, f"nodes.{node_index}.evidence"
                        ),
                    }
                )
            )
        edges = [
            edge.model_copy(
                update={
                    "evidence": normalize(edge.evidence, f"edges.{edge_index}.evidence")
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
