"""Source assertion comparison used by preview and graph publication."""

import json
import re
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


def compare_assertions(
    new_facts: list[dict[str, Any]], existing: list[dict[str, Any]],
    source_anchors: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return support pairs, review candidates, and source-backed preview rows."""
    old_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for old in existing:
        old_by_key.setdefault(
            (old.get("subjectKey", ""), old.get("factType", ""), old.get("predicate", "")), []
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
            "validFrom": fact.get("validFrom"), "validTo": fact.get("validTo"),
            "sourceAnchors": anchors, "classification": "ADDED",
        }
        key = (fact["subjectKey"], fact["factType"], fact["predicate"])
        for old in old_by_key.get(key, []):
            same = _value(old.get("valueJson")) == row["value"]
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
