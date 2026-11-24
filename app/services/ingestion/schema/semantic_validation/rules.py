"""Deterministic semantic rules. Rule chỉ CONTRADICTION khi chắc chắn; còn lại NOT_APPLICABLE."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Callable

from app.services.ingestion.schema.semantic_validation.models import (
    CODE_NEGATION,
    CODE_NUMERIC,
    CODE_RANGE,
    CODE_RELATION,
    Claim,
    RuleOutcome,
    RuleResult,
)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_TOLERANCE = Decimal("0.000001")

_SCALES = (
    (re.compile(r"\s*(?:nghìn|ngàn|k)\b"), Decimal(1_000)),
    (re.compile(r"\s*(?:triệu|tr)\b"), Decimal(1_000_000)),
    (re.compile(r"\s*(?:tỷ|tỉ)\b"), Decimal(1_000_000_000)),
)
_PERCENT = re.compile(r"\s*(?:%|phần trăm)")
_UNIT_AFTER = re.compile(r"\s*([^\W\d_]+)")

# dimension -> unit -> hệ số về đơn vị gốc
_UNITS: dict[str, dict[str, Decimal]] = {
    "mass": {
        "mg": Decimal("0.001"), "g": Decimal(1), "gam": Decimal(1), "gram": Decimal(1),
        "kg": Decimal(1000), "kilogram": Decimal(1000), "kilôgam": Decimal(1000),
        "tấn": Decimal(1_000_000),
    },
    "length": {
        "mm": Decimal("0.001"), "cm": Decimal("0.01"), "m": Decimal(1), "mét": Decimal(1),
        "km": Decimal(1000),
    },
    "volume": {"ml": Decimal(1), "l": Decimal(1000), "lít": Decimal(1000)},
    "time": {
        "giây": Decimal(1) / 60, "phút": Decimal(1), "giờ": Decimal(60),
    },
    "months": {"tháng": Decimal(1), "quý": Decimal(3), "năm": Decimal(12)},
}
_UNIT_DIM = {unit: dim for dim, table in _UNITS.items() for unit in table}

_NUMBER_WORDS = re.compile(
    r"\b(?:hai|ba|bốn|sáu|bảy|tám|chín|mười|mươi|mốt|rưỡi|nửa)\b"
)

NUMBER_G = r"(\d+(?:[.,]\d+)*)"


# ----------------------------------------------------------------- numbers
def _thousands_ok(parts: list[str]) -> bool:
    return (
        len(parts[0]) <= 3
        and all(len(part) == 3 for part in parts[1:])
    )


def _token_values(token: str) -> set[Decimal]:
    """Mọi cách đọc hợp lý của một token số (VN/EN separators)."""
    if token.isdigit():
        return {Decimal(token)}
    parts = re.split(r"[.,]", token)
    values: set[Decimal] = set()
    if _thousands_ok(parts):
        values.add(Decimal("".join(parts)))
    head = parts[:-1]
    if (len(head) == 1 or _thousands_ok(head)) and parts[-1]:
        values.add(Decimal("".join(head) + "." + parts[-1]))
    return values or {Decimal(part) for part in parts if part}


def _numbers_in(text: str) -> set[Decimal]:
    """Mọi giá trị số có thể suy ra từ text (kèm %, thang nghìn/triệu, đổi đơn vị)."""
    found: set[Decimal] = set()
    lowered = text.casefold()
    for match in _NUMBER.finditer(lowered):
        values = _token_values(match.group())
        tail = lowered[match.end() : match.end() + 24]
        expanded = set(values)
        if _PERCENT.match(tail):
            expanded |= {v / 100 for v in values}
        for pattern, factor in _SCALES:
            if pattern.match(tail):
                expanded |= {v * factor for v in values}
        unit = _UNIT_AFTER.match(tail)
        if unit and unit.group(1) in _UNIT_DIM:
            table = _UNITS[_UNIT_DIM[unit.group(1)]]
            base = {v * table[unit.group(1)] for v in values}
            for factor in table.values():
                expanded |= {b / factor for b in base}
        found |= expanded
    return found


def _same(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= _TOLERANCE * max(Decimal(1), abs(left), abs(right))


def _as_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def numeric_rule(claim: Claim, evidence: str) -> RuleResult:
    """Decimal normalization + exact comparison."""
    name = "numeric"
    value = _as_decimal(claim.value)
    if claim.kind != "property" or value is None:
        return RuleResult(name, RuleOutcome.NOT_APPLICABLE)
    lowered = evidence.casefold()
    if _NUMBER_WORDS.search(lowered) or (value == 1 and "một" in lowered):
        return RuleResult(name, RuleOutcome.NOT_APPLICABLE, detail="number words")
    candidates = _numbers_in(evidence)
    if not candidates:
        return RuleResult(name, RuleOutcome.NOT_APPLICABLE, detail="no digits")
    targets = {value}
    if value != value.to_integral_value() and abs(value) < 1:
        targets.add(value * 100)  # 0.2 <-> 20%
    if any(_same(t, c) for t in targets for c in candidates):
        return RuleResult(name, RuleOutcome.PASS)
    shown = ", ".join(sorted({format(c.normalize(), "f") for c in candidates})[:8])
    return RuleResult(
        name,
        RuleOutcome.CONTRADICTION,
        CODE_NUMERIC,
        f"claim value {value} not in evidence numbers [{shown}]",
    )


# ------------------------------------------------------------------- range
_CUE_OPS = {
    ">=": ("ít nhất", "tối thiểu", "không dưới", "không ít hơn", "không thấp hơn", "≥", ">=", "=>"),
    "<=": ("nhiều nhất", "tối đa", "không quá", "không vượt quá", "không cao hơn", "không nhiều hơn", "≤", "<=", "=<"),
    ">": ("lớn hơn", "nhiều hơn", "cao hơn", "vượt quá", "trên", ">"),
    "<": ("nhỏ hơn", "ít hơn", "thấp hơn", "dưới", "<"),
}
_CUE_LOOKUP = {cue: op for op, cues in _CUE_OPS.items() for cue in cues}
_CUE_PATTERN = re.compile(
    r"(?P<cue>"
    + "|".join(re.escape(c) for c in sorted(_CUE_LOOKUP, key=len, reverse=True))
    + r")\s*(?:là\s*)?"
    + NUMBER_G
)
_POSTFIX = re.compile(NUMBER_G + r"\s*(?:[^\W\d_]+\s+)?(?P<post>trở lên|trở xuống)")
_BETWEEN = re.compile(
    r"(?:từ|giữa)\s*" + NUMBER_G + r"\s*(?:[^\W\d_]+\s+)?(?:đến|tới|-|–|và)\s*" + NUMBER_G
)
_CLAIM_CMP = re.compile(r"^\s*(>=|≥|<=|≤|>|<)\s*" + NUMBER_G + r"\s*$")
_MIN_HINT = re.compile(r"\b(?:min|minimum|tối thiểu|ít nhất)\b")
_MAX_HINT = re.compile(r"\b(?:max|maximum|tối đa|nhiều nhất)\b")


def _evidence_comparators(text: str) -> list[tuple[str, set[Decimal]]]:
    lowered = text.casefold()
    found: list[tuple[str, set[Decimal]]] = []
    for match in _CUE_PATTERN.finditer(lowered):
        found.append((_CUE_LOOKUP[match.group("cue")], _token_values(match.group(2))))
    for match in _POSTFIX.finditer(lowered):
        op = ">=" if match.group("post") == "trở lên" else "<="
        found.append((op, _token_values(match.group(1))))
    for match in _BETWEEN.finditer(lowered):
        found.append((">=", _token_values(match.group(1))))
        found.append(("<=", _token_values(match.group(2))))
    return found


def _claim_comparator(claim: Claim) -> tuple[str, Decimal] | None:
    if isinstance(claim.value, str):
        match = _CLAIM_CMP.match(claim.value)
        if match:
            op = {"≥": ">=", "≤": "<="}.get(match.group(1), match.group(1))
            return op, next(iter(_token_values(match.group(2))))
    value = _as_decimal(claim.value)
    if value is None:
        return None
    hint = f"{claim.predicate_label} {claim.predicate}".casefold()
    if _MIN_HINT.search(hint):
        return ">=", value
    if _MAX_HINT.search(hint):
        return "<=", value
    return None


def range_rule(claim: Claim, evidence: str) -> RuleResult:
    """Interval normalization: ≥12 khác >12, tối thiểu khác tối đa."""
    name = "range"
    comparator = _claim_comparator(claim) if claim.kind == "property" else None
    if comparator is None:
        return RuleResult(name, RuleOutcome.NOT_APPLICABLE)
    op, number = comparator
    related = [
        found_op
        for found_op, values in _evidence_comparators(evidence)
        if any(_same(number, v) for v in values)
    ]
    if not related:
        return RuleResult(name, RuleOutcome.NOT_APPLICABLE)
    if op in related:
        return RuleResult(name, RuleOutcome.PASS)
    return RuleResult(
        name,
        RuleOutcome.CONTRADICTION,
        CODE_RANGE,
        f"claim requires {op} {number} but evidence states {sorted(set(related))}",
    )


# ---------------------------------------------------------------- negation
_NEGATION = re.compile(
    r"\b(?:không được phép|không cho phép|không được|không phải|nghiêm cấm|không|chưa|cấm)\b"
)
_NOT_NEGATION = re.compile(
    r"không\s+(?:chỉ|những|quá|vượt|dưới|ít hơn|thấp hơn|cao hơn|nhiều hơn)"
)
_CLAUSE_SPLIT = re.compile(r"[.;\n|]|,\s|\bnhưng\b")
_LABEL_STOP = {"có", "là", "của", "được", "các", "và", "the", "of", "is", "has"}


def _label_tokens(label: str) -> set[str]:
    return {
        tok
        for tok in re.findall(r"\w+", label.casefold())
        if len(tok) >= 3 and tok not in _LABEL_STOP
    }


def _negated(clause: str) -> bool:
    return bool(_NEGATION.search(_NOT_NEGATION.sub(" ", clause)))


def negation_rule(claim: Claim, evidence: str) -> RuleResult:
    """Claim khẳng định (true) nhưng evidence phủ định đúng mệnh đề đó."""
    name = "negation"
    if claim.kind != "property" or claim.value is not True:
        return RuleResult(name, RuleOutcome.NOT_APPLICABLE)
    tokens = _label_tokens(claim.predicate_label)
    if not tokens:
        return RuleResult(name, RuleOutcome.NOT_APPLICABLE)
    relevant = [
        clause
        for clause in _CLAUSE_SPLIT.split(evidence.casefold())
        if tokens & set(re.findall(r"\w+", clause))
    ]
    if not relevant:
        return RuleResult(name, RuleOutcome.NOT_APPLICABLE)
    if all(_negated(clause) for clause in relevant):
        return RuleResult(
            name,
            RuleOutcome.CONTRADICTION,
            CODE_NEGATION,
            f"evidence negates '{claim.predicate_label}'",
        )
    return RuleResult(name, RuleOutcome.NOT_APPLICABLE)


# ---------------------------------------------------------------- relation
_PASSIVE = re.compile(r"\b(?:được|bị|bởi|do)\b")


def _first_span(text: str, names: tuple[str, ...]) -> tuple[int, int] | None:
    spans = []
    for name in names:
        folded = name.casefold().strip()
        if not folded:
            continue
        match = re.search(rf"(?<!\w){re.escape(folded)}(?!\w)", text)
        if match:
            spans.append(match.span())
    return min(spans) if spans else None


def relation_rule(claim: Claim, evidence: str) -> RuleResult:
    """`A <quan hệ> B` không support `B <quan hệ> A`; nhận diện câu bị động."""
    name = "relation"
    if claim.kind != "edge" or not claim.relation_terms:
        return RuleResult(name, RuleOutcome.NOT_APPLICABLE)
    text = evidence.casefold()
    source = _first_span(text, claim.subject_names)
    target = _first_span(text, claim.object_names)
    if source is None or target is None or source == target:
        return RuleResult(name, RuleOutcome.NOT_APPLICABLE)
    orientations: set[str] = set()
    for term in claim.relation_terms:
        term_folded = term.casefold()
        if len(term_folded) < 2:
            continue
        for match in re.finditer(re.escape(term_folded), text):
            first, second = sorted((source, target))
            if first[1] <= match.start() <= second[0]:
                # "A <term> B" hoặc "A được/bởi B <term>"
                forward = source[0] < target[0]
                if _PASSIVE.search(text[first[1] : match.start()]):
                    forward = not forward
            elif match.start() >= second[1] and _PASSIVE.search(text[first[1] : second[0]]):
                # "B được A <term>": tác nhân là tên đứng ngay trước term
                forward = second == source
            else:
                continue
            orientations.add("forward" if forward else "reverse")
    if "forward" in orientations:
        return RuleResult(name, RuleOutcome.PASS)
    if "reverse" in orientations:
        return RuleResult(
            name, RuleOutcome.CONTRADICTION, CODE_RELATION, "relation direction reversed"
        )
    return RuleResult(name, RuleOutcome.NOT_APPLICABLE)


RULES: tuple[Callable[[Claim, str], RuleResult], ...] = (
    numeric_rule,
    range_rule,
    negation_rule,
    relation_rule,
)


def run_rules(claim: Claim, evidence: str) -> list[RuleResult]:
    return [rule(claim, evidence) for rule in RULES]


def first_contradiction(results: list[RuleResult]) -> RuleResult | None:
    return next((r for r in results if r.outcome is RuleOutcome.CONTRADICTION), None)
