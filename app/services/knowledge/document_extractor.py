"""Document & Rich-Text Extractor for Word (.docx) and Markdown/Text (.md, .txt).

No PDF parsing is included (only Word .docx and Markdown/plain text).
Normalizes extracted sentences into structured ontology (entity_type, predicate) pairs
so DataOwnershipValidator operates purely on schema & predicate metadata.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

from app.services.knowledge.governance_pipeline import CandidateFact

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Layer 1 operational claims extracted by the Document Extractor into their canonical Layer 1 (entity_type, predicate)
_LAYER_1_EXTRACTION_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "class_schedule",
        "start_time",
        re.compile(
            r"(?i)\b(?:lớp|ca\s*học|lịch\s*(?:học|tập)|thứ\s*[2-7]|chủ\s*nhật).{0,40}\b\d{1,2}[:h]\d{2}\b"
        ),
    ),
    (
        "training_plan",
        "final_price",
        re.compile(
            r"(?i)\b(?:học\s*phí|gói\s*(?:tập|học)|giá\s*gói|đơn\s*giá).{0,40}\b\d[\d.,]{2,}\s*(?:đ|vnd|vnđ|k|nghìn|triệu)\b"
        ),
    ),
    (
        "course",
        "capacity",
        re.compile(
            r"(?i)\b(?:sĩ\s*số|sức\s*chứa|tối\s*đa)\s*(?:là|:)?\s*\d+\s*(?:học\s*viên|em|người|bạn)\b"
        ),
    ),
    (
        "branch",
        "hotline",
        re.compile(
            r"(?i)\b(?:hotline|số\s*điện\s*thoại\s*cơ\s*sở)\s*(?:là|:)?\s*0\d{8,10}\b"
        ),
    ),
    (
        "belt_exam",
        "exam_result",
        re.compile(
            r"(?i)\b(?:học\s*viên|em|bé)\s+[A-ZÀ-Ỹ][a-zà-ỹ]+.{0,35}\b(?:đạt|trượt|kết\s*quả\s*thi\s*đai|lên\s*đai\s*quý)\b"
        ),
    ),
)

_ENTITY_KEYWORDS: tuple[tuple[str, str, str], ...] = (
    ("center_profile", "history", r"(?i)\b(?:lịch\s*sử|tầm\s*nhìn|sứ\s*mệnh|giới\s*thiệu|thành\s*lập)\b"),
    ("facility_item", "description", r"(?i)\b(?:thảm|phòng\s*tập|cơ\s*sở\s*vật\s*chất|trang\s*thiết\s*bị|bảo\s*hộ)\b"),
    ("enrollment_step", "description", r"(?i)\b(?:bước\s*\d+|quy\s*trình\s*đăng\s*ký|hồ\s*sơ\s*nhập\s*học)\b"),
    ("belt_rank", "exam_requirements", r"(?i)\b(?:đai\s*(?:trắng|vàng|xanh|đỏ|đen)|thăng\s*đai|cấp\s*đai|thi\s*lên\s*đai)\b"),
    ("poomsae", "description", r"(?i)\b(?:bài\s*quyền|poomsae|taegeuk|thái\s*cực)\b"),
    ("taekwondo_concept", "description", r"(?i)\b(?:kỹ\s*thuật|đòn|lợi\s*ích|thể\s*lực|kỷ\s*luật|tự\s*vệ)\b"),
)

_SCOPE_DEFAULT_ENTITY_PREDICATE: dict[str, tuple[str, str]] = {
    "center_overview": ("center_profile", "history"),
    "admission_onboarding": ("policy_rule", "summary"),
    "training_knowledge": ("taekwondo_concept", "description"),
    "belt_and_exam": ("belt_rank", "exam_requirements"),
    "tuition_and_discount": ("policy_rule", "summary"),
}


def extract_text_from_document(filename: str, raw_bytes: bytes) -> str:
    """Extract clean UTF-8 text from .docx or .md/.txt files."""
    lower = (filename or "").lower()
    if lower.endswith(".docx"):
        return _extract_docx_text(raw_bytes)
    if lower.endswith((".md", ".markdown", ".txt")):
        return raw_bytes.decode("utf-8", errors="replace").strip()
    raise ValueError("Chỉ hỗ trợ tệp Word (.docx) hoặc Markdown/Text (.md, .txt).")


def _extract_docx_text(raw_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            if "word/document.xml" not in zf.namelist():
                raise ValueError("Tệp .docx không hợp lệ (thiếu word/document.xml).")
            xml_bytes = zf.read("word/document.xml")
    except zipfile.BadZipFile as exc:
        raise ValueError("Tệp .docx bị hỏng hoặc không đúng định dạng Word.") from exc

    root = ElementTree.fromstring(xml_bytes)
    paragraphs: list[str] = []
    for p in root.iter(f"{_W_NS}p"):
        texts = [t.text for t in p.iter(f"{_W_NS}t") if t.text]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    for tc in root.iter(f"{_W_NS}tc"):
        cell_texts = [t.text for t in tc.iter(f"{_W_NS}t") if t.text]
        cell_line = " ".join("".join(cell_texts).split())
        if cell_line and cell_line not in paragraphs:
            paragraphs.append(cell_line)
    return "\n".join(paragraphs).strip()


def infer_entity_and_predicate(statement: str, scope_key: str) -> tuple[str, str]:
    """Classify an unstructured document statement into its ontology (entity_type, predicate)."""
    for entity_type, predicate, pattern in _LAYER_1_EXTRACTION_RULES:
        if pattern.search(statement):
            return entity_type, predicate
    for entity_type, predicate, pattern_str in _ENTITY_KEYWORDS:
        if re.search(pattern_str, statement):
            return entity_type, predicate
    return _SCOPE_DEFAULT_ENTITY_PREDICATE.get(scope_key, ("policy_rule", "summary"))


def infer_entity_type(statement: str, scope_key: str) -> str:
    """Classify a statement into its ontology entity_type."""
    entity_type, _ = infer_entity_and_predicate(statement, scope_key)
    return entity_type


def extract_candidate_facts(
    *,
    tenant_id: str,
    scope_key: str,
    raw_text: str,
    item_id: str | None = None,
    source_type: str = "DOCUMENT",
) -> list[CandidateFact]:
    """Split prose/markdown into grounded CandidateFacts with structured (entity_type, predicate)."""
    import uuid

    doc_key = (item_id or "").strip() or uuid.uuid4().hex[:12]
    segments = [
        seg.strip()
        for seg in re.split(r"(?<=[.!?。\n])\s+", raw_text or "")
        if seg and seg.strip()
    ]
    if not segments and raw_text.strip():
        segments = [raw_text.strip()]

    facts: list[CandidateFact] = []
    for idx, seg in enumerate(segments):
        cleaned = re.sub(r"^#{1,6}\s+", "", seg).strip()
        if not cleaned:
            continue
        entity_type, predicate = infer_entity_and_predicate(cleaned, scope_key)
        facts.append(
            CandidateFact(
                fact_id=f"{tenant_id}:{doc_key}:ext:{idx}",
                entity_type=entity_type,
                property_name=predicate,
                predicate=predicate,
                fact_type="PROPERTY",
                subject_key=f"{tenant_id}:{entity_type}:{doc_key}:{idx}",
                source_type=source_type,
                statement=cleaned,
                scope_key=scope_key,
            )
        )
    return facts


__all__ = [
    "extract_candidate_facts",
    "extract_text_from_document",
    "infer_entity_and_predicate",
    "infer_entity_type",
]
