"""Deterministic template serialization: fact/edge -> canonical Vietnamese claim.

Không dùng LLM, không random: cùng một fact luôn sinh cùng một claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping

from app.schemas.ingestion.graph_patch import GraphPatchFragment
from app.services.ingestion.schema.semantic_validation.models import (
    Claim,
    ClaimUnit,
    EvidenceItem,
)

_LABEL_KEYS = ("displayName", "label", "viName", "name", "title")
_NAME_KEY_HINT = re.compile(r"name|ten|title", re.IGNORECASE)
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?$")
_RANGE_KEYS = (("min", "max"), ("from", "to"), ("gte", "lte"), ("start", "end"))


def humanize(name: str) -> str:
    """`minimumAge` / `minimum_age` -> `minimum age`."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return re.sub(r"[_\-\s]+", " ", spaced).strip().lower()


def _label_of(item: Mapping[str, Any]) -> str | None:
    for key in _LABEL_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@dataclass(frozen=True)
class LabelCatalog:
    """Nhãn tiếng Việt của ontology (tuỳ chọn); thiếu thì fallback humanize."""

    classes: Mapping[str, str] = field(default_factory=dict)
    properties: Mapping[str, str] = field(default_factory=dict)
    units: Mapping[str, str] = field(default_factory=dict)
    relations: Mapping[str, str] = field(default_factory=dict)
    relation_aliases: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_projection(cls, projection: Any) -> "LabelCatalog":
        classes: dict[str, str] = {}
        properties: dict[str, str] = {}
        units: dict[str, str] = {}
        relations: dict[str, str] = {}
        aliases: dict[str, list[str]] = {}
        rel_ids: dict[str, str] = {}
        for item in getattr(projection, "entity_types", []) or []:
            label = _label_of(item)
            if label and item.get("technicalName"):
                classes[item["technicalName"]] = label
        for item in getattr(projection, "properties", []) or []:
            tech = item.get("technicalName")
            if not tech:
                continue
            key = f"{item.get('entityType')}.{tech}"
            label = _label_of(item)
            if label:
                properties[key] = label
                properties.setdefault(tech, label)
            unit = item.get("unit")
            if isinstance(unit, str) and unit.strip():
                units[key] = unit.strip()
                units.setdefault(tech, unit.strip())
        for item in getattr(projection, "relationships", []) or []:
            tech = item.get("technicalName")
            if not tech:
                continue
            rel_ids[str(item.get("id"))] = tech
            label = _label_of(item)
            if label:
                relations[tech] = label
        for alias in getattr(projection, "aliases", []) or []:
            if alias.get("targetType") != "RELATIONSHIP":
                continue
            tech = rel_ids.get(str(alias.get("targetId")))
            value = alias.get("alias")
            if tech and isinstance(value, str) and value.strip():
                aliases.setdefault(tech, []).append(value.strip())
        return cls(
            classes,
            properties,
            units,
            relations,
            {key: tuple(vals) for key, vals in aliases.items()},
        )

    def class_label(self, class_name: str) -> str:
        return self.classes.get(class_name) or humanize(class_name)

    def property_label(self, class_name: str, prop: str) -> str:
        return (
            self.properties.get(f"{class_name}.{prop}")
            or self.properties.get(prop)
            or humanize(prop)
        )

    def property_unit(self, class_name: str, prop: str) -> str | None:
        return self.units.get(f"{class_name}.{prop}") or self.units.get(prop)

    def relation_label(self, edge_name: str) -> str:
        return self.relations.get(edge_name) or humanize(edge_name)

    def relation_terms(self, edge_name: str) -> tuple[str, ...]:
        terms = [
            self.relation_label(edge_name),
            humanize(edge_name),
            *self.relation_aliases.get(edge_name, ()),
        ]
        seen: dict[str, None] = {}
        for term in terms:
            if term and term.casefold() not in seen:
                seen[term.casefold()] = None
        return tuple(seen)


_EMPTY_CATALOG = LabelCatalog()


def _format_number(value: int | float | Decimal) -> str:
    number = Decimal(str(value))
    if number == number.to_integral_value():
        integer = int(number)
        text = f"{abs(integer):,}".replace(",", ".") if abs(integer) >= 10000 else str(abs(integer))
        return f"-{text}" if integer < 0 else text
    text = format(number.normalize(), "f")
    return text.replace(".", ",")


def format_value(value: Any) -> str | None:
    """Chuẩn hoá giá trị thành chuỗi hiển thị; None nếu không có giá trị."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "có" if value else "không"
    if isinstance(value, (int, float, Decimal)):
        return _format_number(value)
    if isinstance(value, (datetime, date)):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        match = _ISO_DATE.match(text)
        if match:
            year, month, day = match.groups()
            return f"{day}/{month}/{year}"
        return text
    if isinstance(value, Mapping):
        for low_key, high_key in _RANGE_KEYS:
            if low_key in value or high_key in value:
                low, high = format_value(value.get(low_key)), format_value(value.get(high_key))
                if low and high:
                    return f"từ {low} đến {high}"
                return low or high
        parts = [format_value(item) for item in value.values()]
        return ", ".join(part for part in parts if part) or None
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        if isinstance(value, (set, frozenset)):
            items = sorted(items, key=str)
        parts = [format_value(item) for item in items]
        return ", ".join(part for part in parts if part) or None
    return str(value)


def _identity_names(identity: Mapping[str, Any], fallback: str) -> tuple[str, ...]:
    ordered = sorted(
        identity.items(), key=lambda kv: (0 if _NAME_KEY_HINT.search(str(kv[0])) else 1)
    )
    names = [
        str(value).strip()
        for _, value in ordered
        if isinstance(value, (str, int, float))
        and not isinstance(value, bool)
        and str(value).strip()
    ]
    return tuple(names) or (fallback,)


def _subject_text(class_label: str, names: tuple[str, ...]) -> str:
    joined = " ".join(names)
    if joined.casefold().startswith(class_label.casefold()):
        return joined
    return f"{class_label} {joined}".strip()


def build_property_claim(
    *,
    class_name: str,
    identity: Mapping[str, Any],
    property_name: str,
    value: Any,
    location: str,
    fallback_subject: str = "",
    labels: LabelCatalog | None = None,
) -> Claim | None:
    """`Đai đỏ có độ tuổi tối thiểu là 12 tuổi.` Trả None nếu value rỗng."""
    catalog = labels or _EMPTY_CATALOG
    rendered = format_value(value)
    if rendered is None:
        return None
    names = _identity_names(identity, fallback_subject or class_name)
    subject = _subject_text(catalog.class_label(class_name), names)
    label = catalog.property_label(class_name, property_name)
    if isinstance(value, bool):
        text = f"{subject} có {label}." if value else f"{subject} không có {label}."
    else:
        unit = catalog.property_unit(class_name, property_name)
        shown = f"{rendered} {unit}" if unit and isinstance(value, (int, float, Decimal)) else rendered
        text = f"{subject} có {label} là {shown}."
    return Claim(
        kind="property",
        location=location,
        text=text,
        predicate=property_name,
        predicate_label=label,
        subject_names=names,
        value=value,
    )


def build_edge_claim(
    *,
    edge_name: str,
    source_class: str,
    source_identity: Mapping[str, Any],
    source_fallback: str,
    target_class: str,
    target_identity: Mapping[str, Any],
    target_fallback: str,
    location: str,
    labels: LabelCatalog | None = None,
) -> Claim:
    """`Huấn luyện viên A phụ trách lớp Nâng cao.`"""
    catalog = labels or _EMPTY_CATALOG
    source_names = _identity_names(source_identity, source_fallback)
    target_names = _identity_names(target_identity, target_fallback)
    source = _subject_text(catalog.class_label(source_class), source_names)
    target = _subject_text(catalog.class_label(target_class), target_names)
    relation = catalog.relation_label(edge_name)
    return Claim(
        kind="edge",
        location=location,
        text=f"{source} {relation} {target}.",
        predicate=edge_name,
        predicate_label=relation,
        subject_names=source_names,
        object_names=target_names,
        relation_terms=catalog.relation_terms(edge_name),
    )


def _evidence_items(evidences: Iterable[Any], location: str) -> tuple[EvidenceItem, ...]:
    return tuple(
        EvidenceItem(f"{location}.evidence.{index}", ev.chunk_index, ev.text)
        for index, ev in enumerate(evidences)
        if (ev.text or "").strip()
    )


def build_claims(
    fragment: GraphPatchFragment, labels: LabelCatalog | None = None
) -> list[ClaimUnit]:
    """Mọi property và edge của fragment -> ClaimUnit (claim + evidence đã resolve)."""
    nodes = {node.temp_id: node for node in fragment.nodes}
    units: list[ClaimUnit] = []
    for n_idx, node in enumerate(fragment.nodes):
        for p_idx, prop in enumerate(node.properties):
            location = f"nodes.{n_idx}.properties.{p_idx}"
            claim = build_property_claim(
                class_name=node.class_name,
                identity=node.identity,
                property_name=prop.property_name,
                value=prop.value,
                location=location,
                fallback_subject=node.temp_id,
                labels=labels,
            )
            evidence = _evidence_items(prop.evidence, location)
            if claim is not None and evidence:
                units.append(ClaimUnit(claim, evidence))
    for e_idx, edge in enumerate(fragment.edges):
        source, target = nodes.get(edge.source_temp_id), nodes.get(edge.target_temp_id)
        if source is None or target is None:
            continue
        location = f"edges.{e_idx}"
        claim = build_edge_claim(
            edge_name=edge.edge_name,
            source_class=source.class_name,
            source_identity=source.identity,
            source_fallback=source.temp_id,
            target_class=target.class_name,
            target_identity=target.identity,
            target_fallback=target.temp_id,
            location=location,
            labels=labels,
        )
        evidence = _evidence_items(edge.evidence, location)
        if evidence:
            units.append(ClaimUnit(claim, evidence))
    return units
