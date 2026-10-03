"""Deterministic logical-fact references used by coverage and readiness checks."""

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from app.schemas.ingestion_schema import (
    GraphNode,
    GraphPatchFragment,
    SemanticGraphPatchFragment,
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def value_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:16]


def property_fact_ref(node_key: str, property_name: str, value: Any) -> str:
    return f"property:{node_key}:{property_name}:{value_digest(value)}"


def edge_fact_ref(edge_name: str, source_key: str, target_key: str, properties: Any) -> str:
    return (
        f"edge:{edge_name}:{source_key}:{target_key}:"
        f"{value_digest(properties or {})}"
    )


def normalize_match_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"[^\w]+", "", text, flags=re.UNICODE)


def _display_values(node: GraphNode) -> list[str]:
    values = [str(value) for value in node.identity.values() if value not in (None, "")]
    for fact in node.properties:
        if fact.property_name in {"name", "full_name", "title", "code"}:
            values.append(str(fact.value))
    return list(dict.fromkeys(values))


def build_fact_index(
    fragments: Iterable[GraphPatchFragment],
) -> dict[str, dict[str, Any]]:
    """Index facts without merging away conflicting values."""

    result: dict[str, dict[str, Any]] = {}
    node_displays: dict[str, list[str]] = {}
    materialized = list(fragments)
    for fragment in materialized:
        for node in fragment.nodes:
            node_displays.setdefault(node.temp_id, _display_values(node))
            for fact in node.properties:
                ref = property_fact_ref(node.temp_id, fact.property_name, fact.value)
                result[ref] = {
                    "factRef": ref,
                    "factType": "PROPERTY",
                    "subjectKey": node.temp_id,
                    "propertyName": fact.property_name,
                    "value": fact.value,
                }
    for fragment in materialized:
        for edge in fragment.edges:
            ref = edge_fact_ref(
                edge.edge_name,
                edge.source_temp_id,
                edge.target_temp_id,
                edge.properties,
            )
            result[ref] = {
                "factRef": ref,
                "factType": "RELATIONSHIP",
                "edgeName": edge.edge_name,
                "sourceKey": edge.source_temp_id,
                "targetKey": edge.target_temp_id,
                "sourceDisplays": node_displays.get(edge.source_temp_id, []),
                "targetDisplays": node_displays.get(edge.target_temp_id, []),
            }
    return result


def add_local_fact_aliases(
    fact_index: dict[str, dict[str, Any]],
    semantic: SemanticGraphPatchFragment,
    canonical: GraphPatchFragment,
) -> None:
    """Add predictable references for facts declared in the current submission."""

    for semantic_node, canonical_node in zip(
        semantic.nodes, canonical.nodes, strict=True
    ):
        property_counts: dict[str, int] = {}
        for fact in semantic_node.properties:
            property_counts[fact.property_name] = property_counts.get(fact.property_name, 0) + 1
        for fact in canonical_node.properties:
            if property_counts.get(fact.property_name) != 1:
                continue
            canonical_ref = property_fact_ref(
                canonical_node.temp_id, fact.property_name, fact.value
            )
            indexed = fact_index.get(canonical_ref)
            if indexed:
                fact_index[
                    f"local-property:{semantic_node.temp_id}:{fact.property_name}"
                ] = indexed
    for edge_index, edge in enumerate(canonical.edges):
        canonical_ref = edge_fact_ref(
            edge.edge_name,
            edge.source_temp_id,
            edge.target_temp_id,
            edge.properties,
        )
        indexed = fact_index.get(canonical_ref)
        if indexed:
            fact_index[f"local-edge:{edge_index}"] = indexed


def duplicate_quote_matches(fact: dict[str, Any], quote: str) -> bool:
    normalized_quote = normalize_match_text(quote)
    if not normalized_quote:
        return False
    if fact.get("factType") == "PROPERTY":
        value = fact.get("value")
        values = value if isinstance(value, list) else [value]
        return any(
            normalized and normalized in normalized_quote
            for normalized in (normalize_match_text(item) for item in values)
        )
    source_values = [
        normalize_match_text(item) for item in fact.get("sourceDisplays", [])
    ]
    target_values = [
        normalize_match_text(item) for item in fact.get("targetDisplays", [])
    ]
    return any(item and item in normalized_quote for item in source_values) and any(
        item and item in normalized_quote for item in target_values
    )


__all__ = [
    "add_local_fact_aliases",
    "build_fact_index",
    "canonical_json",
    "duplicate_quote_matches",
    "edge_fact_ref",
    "normalize_match_text",
    "property_fact_ref",
]
