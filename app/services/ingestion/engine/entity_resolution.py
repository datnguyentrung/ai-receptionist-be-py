"""Resolve extracted entities against committed canonical knowledge.

This module deliberately separates deterministic identity from semantic hints.
Semantic matches are candidates for human review; they never change a canonical
key on their own.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

Decision = Literal["EXACT_REUSE", "DETERMINISTIC_REUSE", "REVIEW_REQUIRED", "CREATE"]


@dataclass(frozen=True)
class EntityResolution:
    extracted_key: str
    canonical_key: str | None
    decision: Decision
    candidate_label: str | None
    confidence: float | None
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "extractedKey": self.extracted_key,
            "canonicalKey": self.canonical_key,
            "decision": self.decision,
            "candidateLabel": self.candidate_label,
            "confidence": self.confidence,
            "reasons": self.reasons,
        }


def resolve_entities(
    nodes: list[dict[str, Any]], candidates_by_key: dict[str, list[dict[str, Any]]]
) -> list[EntityResolution]:
    """Return safe canonical-key decisions for the extracted entity payload."""
    decisions: list[EntityResolution] = []
    for node in nodes:
        key = str(node["stableKey"])
        candidates = candidates_by_key.get(key, [])
        exact = next((item for item in candidates if item.get("stableKey") == key), None)
        if exact:
            decisions.append(_resolution(node, exact, "EXACT_REUSE", 1.0, ["Khóa định danh trùng khớp."]))
            continue
        contact = _contact_value(node)
        if contact:
            same = [item for item in candidates if _contact_value(item) == contact]
            if len(same) == 1:
                decisions.append(_resolution(node, same[0], "DETERMINISTIC_REUSE", 1.0, [
                    "Giá trị kênh liên hệ đã tồn tại sau khi chuẩn hóa.",
                    "Loại kênh khác cách gọi chỉ được xem là alias, không tạo thực thể mới.",
                ]))
                continue
        fee = _fee_signature(node)
        if fee:
            same = [item for item in candidates if _same_fee_schedule(fee, _fee_signature(item))]
            if len(same) == 1:
                decisions.append(_resolution(node, same[0], "DETERMINISTIC_REUSE", 1.0, [
                    "Địa điểm, gói học phí và số tiền trùng khớp sau khi chuẩn hóa."
                ]))
                continue
            if len(same) > 1:
                decisions.append(_resolution(node, same[0], "REVIEW_REQUIRED", None, [
                    "Có nhiều gói học phí cùng kỳ hạn và số tiền; cần xác nhận địa điểm/lớp áp dụng."
                ]))
                continue
        # Policies are intentionally conservative: overlapping wording is a review,
        # never an automatic merge of a broad policy with a specific one.
        if _kind(node) == "policy":
            overlapping = [item for item in candidates if _token_overlap(node, item) >= 0.18]
            if overlapping:
                decisions.append(_resolution(node, overlapping[0], "REVIEW_REQUIRED", None, [
                    "Có policy hiện hữu gần nghĩa; cần xác nhận phạm vi có tương đương hay chỉ chồng lấp."
                ]))
                continue
        decisions.append(EntityResolution(key, None, "CREATE", None, None, ["Không có ứng viên đủ điều kiện để tái sử dụng."]))
    return decisions


def _resolution(node: dict[str, Any], candidate: dict[str, Any], decision: Decision,
                confidence: float | None, reasons: list[str]) -> EntityResolution:
    return EntityResolution(str(node["stableKey"]), str(candidate["stableKey"]), decision,
                            _label(candidate), confidence, reasons)


def _kind(node: dict[str, Any]) -> str:
    return str(node.get("className") or node.get("entityType") or "").casefold()


def _values(node: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    raw_identity = node.get("identity") or node.get("identityJson") or {}
    if isinstance(raw_identity, str):
        try:
            raw_identity = json.loads(raw_identity)
        except ValueError:
            raw_identity = {}
    values.update(raw_identity if isinstance(raw_identity, dict) else {})
    raw_props = node.get("properties") or node.get("propertiesJson") or {}
    if isinstance(raw_props, str):
        try:
            raw_props = json.loads(raw_props)
        except ValueError:
            raw_props = {}
    if isinstance(raw_props, list):
        values.update({str(item.get("propertyName")): item.get("value") for item in raw_props})
    elif isinstance(raw_props, dict):
        values.update(raw_props)
    return values


def _contact_value(node: dict[str, Any]) -> str | None:
    if _kind(node) != "contact_channel":
        return None
    value = str(_values(node).get("value") or "").strip()
    if not value:
        return None
    if "@" in value:
        return value.casefold()
    digits = re.sub(r"\D", "", value)
    return digits or _norm(value)


def _fee_signature(node: dict[str, Any]) -> tuple[str, str, str] | None:
    if _kind(node) != "fee":
        return None
    values = _values(node)
    text = " ".join(str(values.get(key, "")) for key in ("name", "billing_period", "description"))
    normalized = _norm(text)
    venue = "OUTDOOR" if "ngoai troi" in normalized else "GYM" if "phong tap" in normalized else ""
    plan = ("QUARTERLY" if any(word in normalized for word in ("quy", "3 thang")) else
            "WEEKLY_ATTENDANCE" if any(word in normalized for word in ("1 buoi tuan", "tuan")) else
            "MONTHLY" if "thang" in normalized else "")
    amount = _money(values.get("amount"))
    return (venue, plan, amount) if plan and amount else None


def _same_fee_schedule(
    left: tuple[str, str, str], right: tuple[str, str, str] | None
) -> bool:
    if right is None or left[1:] != right[1:]:
        return False
    # Venue is an additional discriminator only when both representations know it.
    return not (left[0] and right[0] and left[0] != right[0])


def _money(value: Any) -> str:
    """Normalize integer, float and formatted Vietnamese currency without rounding."""
    text = str(value or "").strip().replace(" ", "")
    if not text:
        return ""
    digits_only = re.sub(r"[^0-9.,]", "", text)
    if not digits_only:
        return ""
    if "," in digits_only and "." in digits_only:
        digits_only = digits_only.replace(".", "").replace(",", ".")
    elif digits_only.count(".") > 1 or ("." in digits_only and len(digits_only.rsplit(".", 1)[1]) == 3):
        digits_only = digits_only.replace(".", "")
    try:
        amount = Decimal(digits_only)
    except InvalidOperation:
        return ""
    normalized = format(amount.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _label(node: dict[str, Any]) -> str:
    values = _values(node)
    return str(values.get("name") or values.get("value") or node.get("searchText") or node.get("stableKey"))


def _token_overlap(left: dict[str, Any], right: dict[str, Any]) -> float:
    a = set(_norm(" ".join(map(str, _values(left).values()))).split())
    b = set(_norm(" ".join(map(str, _values(right).values()))).split())
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()
