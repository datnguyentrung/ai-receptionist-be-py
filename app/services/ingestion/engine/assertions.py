"""Source assertion comparison used by preview and graph publication."""

import json
import re
import hashlib
import unicodedata
from datetime import date
from typing import Any


_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def temporal_interval(predicate: str, value: Any, evidence: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Keep only an unambiguous, explicit year; unknown time stays unknown."""
    years: set[int] = set()
    if predicate.casefold() in {"year", "event_year", "achievement_year"}:
        years.update(int(item) for item in _YEAR.findall(str(value)))
    for item in evidence:
        years.update(int(year) for year in _YEAR.findall(str(item.get("text", ""))))
    if len(years) != 1:
        return None, None
    year = years.pop()
    return date(year, 1, 1).isoformat(), date(year + 1, 1, 1).isoformat()


def _value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def canonical_value(value: Any) -> Any:
    """Return a deterministic, conservative representation for assertion identity."""
    value = _value(value)
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFKC", value).strip().split())
    if isinstance(value, list):
        return [canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): canonical_value(item) for key, item in sorted(value.items())}
    return value


def equivalent_value(predicate: str, left: Any, right: Any) -> bool:
    """Small, explicit synonym set for deterministic identity attributes.

    This is intentionally not a generic semantic merge.  Generic semantic
    similarity remains a candidate/review signal.
    """
    if canonical_value(left) == canonical_value(right):
        return True
    if predicate.casefold() == "channel_type":
        aliases = {"hotline", "duong day nong", "hot line"}
        def normalized(item: Any) -> str:
            text = unicodedata.normalize("NFKD", str(item).casefold())
            text = "".join(char for char in text if not unicodedata.combining(char))
            return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())
        return normalized(left) in aliases and normalized(right) in aliases
    return False


def assertion_key(
    *,
    fact_type: str,
    subject_key: str,
    predicate: str,
    value: Any,
    object_key: str | None = None,
    identity_qualifiers: dict[str, Any] | None = None,
) -> str:
    """Stable canonical identity. Provenance and generic validity dates stay outside it."""
    material = {
        "factType": fact_type,
        "subjectKey": subject_key,
        "predicate": predicate,
        "objectKey": object_key,
        "value": canonical_value(value),
        "identityQualifiers": canonical_value(identity_qualifiers or {}),
    }
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def compare_assertions(
    new_facts: list[dict[str, Any]], existing: list[dict[str, Any]],
    source_anchors: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return support pairs, review candidates, and source-backed preview rows."""
    old_by_key: dict[tuple[str, str, str, str | None], list[dict[str, Any]]] = {}
    for old in existing:
        old_by_key.setdefault(
            (
                old.get("subjectKey", ""), old.get("factType", ""),
                old.get("predicate", ""), old.get("objectKey"),
            ), []
        ).append(old)
    supports: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for fact in new_facts:
        anchors = sorted({source_anchors[chunk_id] for chunk_id in fact.get("evidenceChunkIds", []) if chunk_id in source_anchors})
        row = {
            "factId": fact["factId"], "factType": fact["factType"],
            "statement": fact["statement"], "predicate": fact["predicate"],
            "value": _value(fact["valueJson"]), "subjectKey": fact["subjectKey"],
            "objectKey": fact.get("objectKey"),
            "validFrom": fact.get("validFrom"), "validTo": fact.get("validTo"),
            "sourceAnchors": anchors, "classification": "ADDED",
        }
        key = (fact["subjectKey"], fact["factType"], fact["predicate"], fact.get("objectKey"))
        for old in old_by_key.get(key, []):
            same = equivalent_value(fact["predicate"], old.get("valueJson"), row["value"])
            old_from, old_to = old.get("validFrom"), old.get("validTo")
            new_from, new_to = fact.get("validFrom"), fact.get("validTo")
            disjoint = bool(old_from and old_to and new_from and new_to and
                            (old_to <= new_from or new_to <= old_from))
            pair = {"newFactId": fact["factId"], "oldFactId": old["factId"]}
            if same and not disjoint:
                supports.append(pair)
                row["classification"] = "SUPPORTED"
            elif fact["factType"] == "PROPERTY" and not isinstance(row["value"], list) and not isinstance(_value(old.get("valueJson")), list) and not disjoint:
                conflicts.append({
                    **pair, "subjectKey": fact["subjectKey"],
                    "predicate": fact["predicate"], "newValue": row["value"],
                    "oldValue": _value(old.get("valueJson")),
                    "newSourceAnchors": anchors, "oldDocument": old.get("documentName"),
                    "oldVersionId": old.get("versionId"),
                    "oldSourceAnchors": old.get("sourceAnchors") or [],
                    "newValidFrom": new_from, "newValidTo": new_to,
                    "oldValidFrom": old_from, "oldValidTo": old_to,
                    "timeUncertain": not all((new_from, new_to, old_from, old_to)),
                })
                row["classification"] = "CONFLICT"
        rows.append(row)
    return supports, conflicts, rows
