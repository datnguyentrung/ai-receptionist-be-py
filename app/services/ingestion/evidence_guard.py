"""Canonicalize evidence excerpts against prepared chunk text before validation."""

import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from app.schemas.ingestion_schema import (
    Evidence,
    EvidenceUnit,
    GraphPatchFragment,
    PreparedChunk,
)

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


def strip_markdown_text(value: str) -> str:
    """Remove markdown styling tokens, heading markers, blockquotes, bullets, and table markers."""
    if not value:
        return ""
    cleaned = re.sub(r"(\*\*|__|`|\*|~~)", "", value)
    cleaned = re.sub(r"^\s*#+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*>+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*[-+*]\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.replace("|", " ")
    return re.sub(r"\s+", " ", cleaned).strip().casefold()


def canonical_markdown_excerpt(chunk: PreparedChunk, quote: str) -> str:
    """Return a chunk line/paragraph when markdown markers are the only mismatch."""
    for surface in _chunk_surfaces(chunk):
        if quote in surface:
            return quote

    target = strip_markdown_text(quote)
    if not target:
        return quote
    for surface in _chunk_surfaces(chunk):
        if target in strip_markdown_text(surface):
            for line in surface.splitlines():
                if target and target in strip_markdown_text(line):
                    return line.strip()
            for paragraph in _paragraphs(surface):
                if target and target in strip_markdown_text(paragraph):
                    return paragraph.strip()
            return surface.strip()
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




@dataclass(frozen=True)
class EvidenceGroundingResult:
    """Result of resolving model evidence against immutable source-owned spans."""

    evidence: Evidence | None
    error_code: str | None = None
    message: str | None = None


class EvidenceGroundingEngine:
    """Single deterministic authority for source provenance.

    New payloads should reference source-owned EvidenceUnit IDs. Legacy free-text
    evidence remains supported temporarily and is canonicalized back to source text.
    """

    _legacy_normalizers: tuple[Callable[[PreparedChunk, str], str], ...] = (
        canonical_whitespace_excerpt,
        canonical_markdown_excerpt,
        canonical_prefix_completion_excerpt,
        canonical_table_excerpt,
        canonical_bullet_excerpt,
        canonical_sentence_window_excerpt,
    )

    def build_units(self, chunk: PreparedChunk) -> list[EvidenceUnit]:
        """Build stable line/block evidence units from the exact prepared chunk text."""
        units: list[EvidenceUnit] = []
        normalized = chunk.text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.splitlines()

        if chunk.section and chunk.section.strip():
            units.append(
                EvidenceUnit(
                    evidence_ref=f"chunk:{chunk.chunk_index}:section",
                    chunk_index=chunk.chunk_index,
                    kind="SECTION",
                    text=chunk.section,
                )
            )

        for line_no, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            units.append(
                EvidenceUnit(
                    evidence_ref=f"chunk:{chunk.chunk_index}:line:{line_no}",
                    chunk_index=chunk.chunk_index,
                    kind="LINE",
                    text=line,
                    start_line=line_no,
                    end_line=line_no,
                )
            )

        block_start: int | None = None
        block_lines: list[str] = []

        def flush_block(end_line: int) -> None:
            nonlocal block_start, block_lines
            if block_start is None or not block_lines:
                block_start = None
                block_lines = []
                return
            # A one-line block duplicates the line unit and adds no value.
            if len(block_lines) > 1:
                units.append(
                    EvidenceUnit(
                        evidence_ref=(
                            f"chunk:{chunk.chunk_index}:block:{block_start}-{end_line}"
                        ),
                        chunk_index=chunk.chunk_index,
                        kind="BLOCK",
                        text="\n".join(block_lines),
                        start_line=block_start,
                        end_line=end_line,
                    )
                )
            block_start = None
            block_lines = []

        for line_no, line in enumerate(lines, start=1):
            if line.strip():
                if block_start is None:
                    block_start = line_no
                block_lines.append(line)
            else:
                flush_block(line_no - 1)
        flush_block(len(lines))

        if not units and chunk.text.strip():
            units.append(
                EvidenceUnit(
                    evidence_ref=f"chunk:{chunk.chunk_index}:block:1-1",
                    chunk_index=chunk.chunk_index,
                    kind="BLOCK",
                    text=chunk.text,
                    start_line=1,
                    end_line=1,
                )
            )
        return units

    def canonicalize_quote(self, chunk: PreparedChunk, quote: str) -> str:
        """Recover an exact source excerpt for legacy free-text evidence."""
        result = quote
        for normalizer in self._legacy_normalizers:
            candidate = normalizer(chunk, result)
            if candidate and any(candidate in surface for surface in _chunk_surfaces(chunk)):
                return candidate
            if candidate:
                result = candidate
        return result

    def resolve(self, chunk: PreparedChunk, evidence: Evidence) -> EvidenceGroundingResult:
        """Resolve evidenceRef authoritatively, or canonicalize legacy evidence text."""
        if evidence.chunk_index != chunk.chunk_index:
            return EvidenceGroundingResult(
                evidence=None,
                error_code="EVIDENCE_CHUNK_MISMATCH",
                message=(
                    f"Evidence references chunk {evidence.chunk_index}, "
                    f"expected {chunk.chunk_index}"
                ),
            )

        if evidence.evidence_ref:
            unit = next(
                (
                    item
                    for item in self.build_units(chunk)
                    if item.evidence_ref == evidence.evidence_ref
                ),
                None,
            )
            if unit is None:
                return EvidenceGroundingResult(
                    evidence=None,
                    error_code="EVIDENCE_REF_NOT_FOUND",
                    message=(
                        f"Evidence ref {evidence.evidence_ref!r} does not exist "
                        f"in chunk {chunk.chunk_index}"
                    ),
                )
            return EvidenceGroundingResult(
                evidence=evidence.model_copy(
                    update={
                        "source": chunk.source_anchor,
                        "section": chunk.section,
                        "text": unit.text,
                    }
                )
            )

        if not evidence.text or not evidence.text.strip():
            return EvidenceGroundingResult(
                evidence=None,
                error_code="EVIDENCE_REQUIRED",
                message="Evidence must provide evidenceRef (preferred) or legacy text",
            )

        canonical = self.canonicalize_quote(chunk, evidence.text)
        if not self.is_grounded(chunk, canonical):
            return EvidenceGroundingResult(
                evidence=None,
                error_code="EVIDENCE_NOT_GROUNDED",
                message=(
                    f"Evidence text {evidence.text!r} is not grounded "
                    f"in chunk {chunk.chunk_index}"
                ),
            )
        return EvidenceGroundingResult(
            evidence=evidence.model_copy(
                update={
                    "source": chunk.source_anchor,
                    "section": chunk.section,
                    "text": canonical,
                }
            )
        )

    def is_grounded(self, chunk: PreparedChunk, quote: str) -> bool:
        """Three deterministic tiers: exact, whitespace/casefold, markdown-stripped."""
        if not quote or not quote.strip():
            return False
        clean_quote = quote.strip()
        surfaces = _chunk_surfaces(chunk)

        if any(clean_quote in surface for surface in surfaces):
            return True

        norm_quote = _collapse_space(clean_quote).casefold()
        if norm_quote and any(
            norm_quote in _collapse_space(surface).casefold() for surface in surfaces
        ):
            return True

        md_quote = strip_markdown_text(clean_quote)
        return bool(md_quote) and any(
            md_quote in strip_markdown_text(surface) for surface in surfaces
        )


class GroundingPolicy(str, Enum):
    """Grounding verification policy for an ontology property."""

    VERBATIM = "VERBATIM"
    NORMALIZED = "NORMALIZED"
    SEMANTIC = "SEMANTIC"


@dataclass(frozen=True)
class PropertyGroundingResult:
    """Explicit result of verifying a property value against its evidence."""

    valid: bool
    policy: str
    error_code: str | None = None
    error_message: str | None = None
    provenance_verified: bool = True
    value_verified: bool = True


def resolve_grounding_policy(contract: dict[str, Any] | None) -> str:
    """Resolve the grounding policy for a property contract.

    Precedence:
    1. Explicit 'groundingPolicy' in property contract constraints.
    2. Data types INTEGER, FLOAT, DATE, DATETIME, BOOLEAN -> NORMALIZED.
    3. Descriptive property names (notes, description, summary, comment, reason, bio, content) -> SEMANTIC.
    4. Default -> VERBATIM (names, codes, emails, phones, identifiers, literals).
    """
    if not contract:
        return GroundingPolicy.VERBATIM.value

    constraints = contract.get("constraints") or {}
    if "groundingPolicy" in constraints:
        policy_str = str(constraints["groundingPolicy"]).strip().upper()
        if policy_str in GroundingPolicy.__members__:
            return policy_str

    data_type = contract.get("dataType", "STRING")
    technical_name = (contract.get("technicalName") or "").strip().lower()
    if (
        data_type in ("INTEGER", "FLOAT", "DATE", "DATETIME", "BOOLEAN")
        or technical_name.endswith(("_date", "_time", "_at"))
    ):
        return GroundingPolicy.NORMALIZED.value

    if technical_name in (
        "notes",
        "description",
        "summary",
        "comment",
        "reason",
        "bio",
        "content",
    ):
        return GroundingPolicy.SEMANTIC.value

    return GroundingPolicy.VERBATIM.value


def validate_property_grounding(
    value: Any,
    evidence_text: str,
    contract: dict[str, Any] | None = None,
) -> PropertyGroundingResult:
    """Deterministic validation of a property value against its evidence under its policy.

    Architecture invariant:
    - Provenance (evidence existence and grounding) is always mandatory.
    - VERBATIM checks literal string presence (exact or normalized whitespace/casefold).
    - NORMALIZED checks deterministic type extraction (integers, floats, dates, booleans).
    - SEMANTIC enforces strict provenance, but delegates semantic natural language extraction
      to the language model without heuristic lexical substring matching.
    """
    policy = resolve_grounding_policy(contract)

    # 1. Provenance check: Evidence text must exist and value must not be None
    if not evidence_text or not evidence_text.strip() or value is None:
        return PropertyGroundingResult(
            valid=False,
            policy=policy,
            error_code="PROPERTY_VALUE_NOT_SUPPORTED_BY_EVIDENCE",
            error_message="Evidence text is empty or property value is None",
            provenance_verified=False,
            value_verified=False,
        )

    # 2. SEMANTIC policy: Code strictly checks provenance; semantic validity is LLM-governed
    if policy == GroundingPolicy.SEMANTIC.value:
        return PropertyGroundingResult(
            valid=True,
            policy=policy,
            error_code=None,
            error_message=None,
            provenance_verified=True,
            value_verified=False,
        )

    # 3. NORMALIZED policy: Deterministic extraction / representation
    if policy == GroundingPolicy.NORMALIZED.value:
        matched = _normalized_value_in_text(value, evidence_text)
        if matched:
            return PropertyGroundingResult(
                valid=True,
                policy=policy,
                error_code=None,
                error_message=None,
                provenance_verified=True,
                value_verified=True,
            )
        prop_name = contract.get("technicalName", "property") if contract else "property"
        return PropertyGroundingResult(
            valid=False,
            policy=policy,
            error_code="PROPERTY_VALUE_NOT_SUPPORTED_BY_EVIDENCE",
            error_message=(
                f"Property '{prop_name}' normalized value {value!r} cannot be "
                f"recovered deterministically from evidence"
            ),
            provenance_verified=True,
            value_verified=False,
        )

    # 4. VERBATIM policy: Literal presence (with whitespace/casing normalization)
    matched = _verbatim_value_in_text(value, evidence_text)
    if matched:
        return PropertyGroundingResult(
            valid=True,
            policy=policy,
            error_code=None,
            error_message=None,
            provenance_verified=True,
            value_verified=True,
        )

    prop_name = contract.get("technicalName", "property") if contract else "property"
    return PropertyGroundingResult(
        valid=False,
        policy=policy,
        error_code="PROPERTY_VALUE_NOT_SUPPORTED_BY_EVIDENCE",
        error_message=(
            f"Property '{prop_name}' value {value!r} cannot be recovered "
            f"deterministically from evidence"
        ),
        provenance_verified=True,
        value_verified=False,
    )


def _verbatim_value_in_text(value: Any, evidence_text: str) -> bool:
    if isinstance(value, list):
        return bool(value) and all(_verbatim_value_in_text(item, evidence_text) for item in value)
    if isinstance(value, dict):
        scalar_values = list(_iter_scalar_values(value))
        return bool(scalar_values) and all(
            _verbatim_value_in_text(item, evidence_text) for item in scalar_values
        )

    rendered = str(value).strip()
    if not rendered:
        return False

    plain_value = _normalize_semantic_surface(rendered)
    plain_evidence = _normalize_semantic_surface(evidence_text)
    if plain_value and plain_value in plain_evidence:
        return True

    compact_value = re.sub(r"[\W_]+", "", plain_value, flags=re.UNICODE)
    compact_evidence = re.sub(r"[\W_]+", "", plain_evidence, flags=re.UNICODE)
    if len(compact_value) >= 4 and compact_value in compact_evidence:
        return True

    iso_date = _parse_iso_date(rendered)
    if iso_date is not None:
        date_forms = {
            iso_date.isoformat(),
            f"{iso_date.day:02d}/{iso_date.month:02d}/{iso_date.year:04d}",
            f"{iso_date.day}/{iso_date.month}/{iso_date.year:04d}",
            f"{iso_date.day:02d}-{iso_date.month:02d}-{iso_date.year:04d}",
            f"{iso_date.day}-{iso_date.month}-{iso_date.year:04d}",
            f"{iso_date.day:02d}.{iso_date.month:02d}.{iso_date.year:04d}",
            f"ngày {iso_date.day} tháng {iso_date.month} năm {iso_date.year}",
        }
        normalized_forms = {_normalize_semantic_surface(item) for item in date_forms}
        if any(item and item in plain_evidence for item in normalized_forms):
            return True

    return False


def _normalized_value_in_text(value: Any, evidence_text: str) -> bool:
    if isinstance(value, list):
        return bool(value) and all(_normalized_value_in_text(item, evidence_text) for item in value)
    if isinstance(value, dict):
        scalar_values = list(_iter_scalar_values(value))
        return bool(scalar_values) and all(
            _normalized_value_in_text(item, evidence_text) for item in scalar_values
        )

    if isinstance(value, bool):
        expected = "true" if value else "false"
        return re.search(rf"(?<!\w){expected}(?!\w)", evidence_text, re.IGNORECASE) is not None

    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return _numeric_value_in_text(value, evidence_text)

    rendered = str(value).strip()
    if not rendered:
        return False

    iso_date = _parse_iso_date(rendered)
    if iso_date is not None:
        plain_evidence = _normalize_semantic_surface(evidence_text)
        date_forms = {
            iso_date.isoformat(),
            f"{iso_date.day:02d}/{iso_date.month:02d}/{iso_date.year:04d}",
            f"{iso_date.day}/{iso_date.month}/{iso_date.year:04d}",
            f"{iso_date.day:02d}-{iso_date.month:02d}-{iso_date.year:04d}",
            f"{iso_date.day}-{iso_date.month}-{iso_date.year:04d}",
            f"{iso_date.day:02d}.{iso_date.month:02d}.{iso_date.year:04d}",
            f"ngày {iso_date.day} tháng {iso_date.month} năm {iso_date.year}",
        }
        normalized_forms = {_normalize_semantic_surface(item) for item in date_forms}
        if any(item and item in plain_evidence for item in normalized_forms):
            return True

    return _verbatim_value_in_text(value, evidence_text)


def property_value_supported_by_evidence(value: Any, evidence_text: str) -> bool:
    """Backward-compatible helper: checks verbatim or normalized support without contract."""
    if not evidence_text or not evidence_text.strip() or value is None:
        return False
    if (
        isinstance(value, bool)
        or (isinstance(value, (int, float, Decimal)) and not isinstance(value, bool))
        or _parse_iso_date(str(value).strip()) is not None
    ):
        return _normalized_value_in_text(value, evidence_text)
    return _verbatim_value_in_text(value, evidence_text)


def _normalize_semantic_surface(value: str) -> str:
    return strip_markdown_text(unicodedata.normalize("NFKC", value))


def _iter_scalar_values(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_scalar_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_scalar_values(item)
    elif value is not None:
        yield value


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _numeric_value_in_text(value: float | Decimal, text: str) -> bool:
    try:
        expected = Decimal(str(value))
    except InvalidOperation:
        return False

    for raw in re.findall(r"(?<!\w)[+-]?\d[\d.,]*(?!\w)", text):
        cleaned = raw.strip()
        candidates: set[str] = {cleaned}
        if re.fullmatch(r"[+-]?\d{1,3}(?:[.,]\d{3})+", cleaned):
            candidates.add(re.sub(r"[.,]", "", cleaned))
        if "," in cleaned and "." not in cleaned:
            candidates.add(cleaned.replace(",", "."))
        for candidate in candidates:
            try:
                if Decimal(candidate) == expected:
                    return True
            except InvalidOperation:
                continue
    return False


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
    "EvidenceGroundingEngine",
    "EvidenceGroundingResult",
    "GraphFragmentEvidenceGuard",
    "GroundingPolicy",
    "PropertyGroundingResult",
    "canonical_bullet_excerpt",
    "canonical_markdown_excerpt",
    "canonical_prefix_completion_excerpt",
    "canonical_table_excerpt",
    "canonical_whitespace_excerpt",
    "property_value_supported_by_evidence",
    "resolve_grounding_policy",
    "validate_property_grounding",
]
