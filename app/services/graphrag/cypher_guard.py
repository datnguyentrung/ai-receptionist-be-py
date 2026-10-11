"""Read-only Cypher validator and value normalizer for Graph QA.

Nguyên tắc phòng vệ theo lớp:
  - Lớp 1 (Module này): Scrub comments/string literals, chặn write keywords,
    chặn multi-statement (dấu ';'), chặn namespace ontology property (p.pskg:...).
  - Lớp 2 (Neo4j Driver): Thực thi với RoutingControl.READ.
"""

from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------
# Regex kiểm tra từ khóa ghi & cú pháp nguy hiểm
# --------------------------------------------------------------------------
_WRITE_KEYWORDS = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP)\b",
    re.IGNORECASE,
)
_MULTI_STATEMENT = re.compile(r";")
_NAMESPACE_PROPERTY = re.compile(r"\.\w+:\w+")  # ví dụ: p.pskg:bankingProductName

# Regex loại bỏ block comment /* ... */ và line comment //...
_STRIP_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRIP_LINE_COMMENT = re.compile(r"//[^\n]*")

# Regex thay thế chuỗi literal ('...' hoặc "...") thành chuỗi rỗng để tránh false positive
_STRIP_STRING_SINGLE = re.compile(r"'(?:[^'\\]|\\.)*'")
_STRIP_STRING_DOUBLE = re.compile(r'"(?:[^"\\]|\\.)*"')


def _scrub(cypher: str) -> str:
    """Loại bỏ comments và string literals trước khi phân tích cú pháp."""
    text = _STRIP_BLOCK_COMMENT.sub(" ", cypher)
    text = _STRIP_LINE_COMMENT.sub(" ", text)
    text = _STRIP_STRING_SINGLE.sub("''", text)
    text = _STRIP_STRING_DOUBLE.sub('""', text)
    return text


def validate_read_cypher(cypher: str) -> None:
    """Kiểm tra câu lệnh Cypher có an toàn để thực thi đọc hay không.

    Raises:
        ValueError: Nếu câu lệnh rỗng, chứa ký tự phân tách đa lệnh (';'),
                    chứa từ khóa thay đổi dữ liệu, hoặc truy cập property có namespace.
    """
    if not cypher or not cypher.strip():
        raise ValueError("Cypher query must not be empty.")

    scrubbed = _scrub(cypher)

    if _MULTI_STATEMENT.search(scrubbed):
        raise ValueError(
            "Multi-statement Cypher is not allowed (remove semicolons ';')."
        )

    write_match = _WRITE_KEYWORDS.search(scrubbed)
    if write_match:
        raise ValueError(
            f"Write keyword '{write_match.group().upper()}' is forbidden in read-only queries."
        )

    ns_match = _NAMESPACE_PROPERTY.search(scrubbed)
    if ns_match:
        raise ValueError(
            f"Namespace property access '{ns_match.group()}' is not allowed; "
            "use the physical neo4jPropertyKey from active schema context."
        )


def normalize_neo4j_value(value: Any) -> Any:
    """Chuyển đổi kiểu dữ liệu đặc thù của Neo4j (Date, Time, Duration, Point...) về JSON-safe."""
    if value is None:
        return None

    # Hỗ trợ neo4j.time.Date, DateTime, Time...
    if hasattr(value, "iso_format"):
        return value.iso_format()

    type_name = type(value).__name__
    if type_name == "Duration":
        return str(value)

    if type_name in {"CartesianPoint", "WGS84Point"}:
        return {
            "srid": getattr(value, "srid", None),
            "x": getattr(value, "x", getattr(value, "longitude", None)),
            "y": getattr(value, "y", getattr(value, "latitude", None)),
            "z": getattr(value, "z", getattr(value, "height", None)),
        }

    if isinstance(value, dict):
        return {k: normalize_neo4j_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [normalize_neo4j_value(v) for v in value]

    return value


__all__ = ["validate_read_cypher", "normalize_neo4j_value"]
