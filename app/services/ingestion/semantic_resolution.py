"""Semantic Entity Resolution and Disambiguation Module.

Cung cấp cơ chế so khớp ngữ nghĩa thực thể (Entity Resolution) dựa trên:
1. Vector Embedding Cosine Similarity (GeminiEmbeddingProvider).
2. LLM Verification (AdkMergeVerifier) để đánh giá ngữ cảnh.
3. Phân luồng 3 cấp độ: AUTO_MERGE (>= 0.95), AMBIGUOUS (0.75 - 0.95 -> Human-in-the-Loop), DISTINCT (< 0.75).
"""

import json
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types

from app.services.graphrag.embeddings import GeminiEmbeddingProvider, cosine_similarity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SemanticCandidate:
    temp_id: str
    class_name: str
    display_name: str
    identity: dict[str, Any]
    properties: dict[str, Any]
    score: float


@dataclass
class DisambiguationPair:
    pair_id: str
    class_name: str
    source_temp_id: str
    source_name: str
    target_temp_id: str
    target_name: str
    similarity: float
    reason: str
    recommended_action: str = "MERGE"  # MERGE or DISTINCT


class AdkMergeVerifier:
    """LLM Verifier sử dụng Gemini để xác minh việc gộp hai thực thể."""

    def __init__(self, *, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or os.getenv(
            "INGESTION_MODEL", os.getenv("GOOGLE_ADK_MODEL", "gemini-2.5-flash")
        )
        api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.client = genai.Client(api_key=api_key) if api_key else genai.Client()

    def verify(
        self,
        *,
        class_name: str,
        entity_a: str,
        entity_b: str,
        score: float,
    ) -> tuple[bool, str]:
        """Đánh giá xem hai thực thể có cùng đại diện cho một đối tượng trong thực tế hay không."""
        prompt = (
            f"Bạn là chuyên gia thẩm định và hợp nhất thực thể tri thức (Entity Resolution) "
            f"cho miền Taekwondo / Võ thuật (Domain: Taekwondo).\n"
            f"Loại thực thể (Class): {class_name}\n"
            f"Thực thể 1: {entity_a}\n"
            f"Thực thể 2: {entity_b}\n"
            f"Độ tương đồng ngữ nghĩa vector: {score:.3f}\n\n"
            f"Nhiệm vụ: Quyết định xem 2 thực thể này có phải là CÙNG MỘT đối tượng thực tế không "
            f"(ví dụ: 'CLB Taekwondo Văn Quán' và 'Hệ Thống Taekwondo Văn Quán' hay 'HLV Phùng Thế Lịch' và 'Võ sư Phùng Thế Lịch').\n"
            f"Trả về kết quả định dạng JSON với các trường:\n"
            f"- 'decision': 'MERGE' (nếu là một thực thể) hoặc 'DISTINCT' (nếu là hai đối tượng khác nhau).\n"
            f"- 'confidence': số thực từ 0.0 đến 1.0.\n"
            f"- 'reason': giải thích ngắn gọn bằng tiếng Việt."
        )

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema={
                        "type": "OBJECT",
                        "properties": {
                            "decision": {
                                "type": "STRING",
                                "enum": ["MERGE", "DISTINCT"],
                            },
                            "confidence": {"type": "NUMBER"},
                            "reason": {"type": "STRING"},
                        },
                        "required": ["decision", "confidence", "reason"],
                    },
                ),
            )
            if not response.text:
                return False, "Không nhận được phản hồi từ mô hình."
            data = json.loads(response.text)
            is_merge = data.get("decision") == "MERGE"
            reason = data.get("reason", "")
            return is_merge, reason
        except Exception as exc:
            logger.warning("[ADK_MERGE_VERIFIER_ERROR] model=%s error=%s", self.model, exc)
            return False, f"Lỗi xác minh: {exc}"


class SemanticEntityResolver:
    """Bộ giải quyết thực thể dựa trên độ tương đồng vector và xác minh LLM."""

    def __init__(
        self,
        *,
        embedding_provider: GeminiEmbeddingProvider | None = None,
        verifier: AdkMergeVerifier | None = None,
        soft_threshold: float = 0.75,
        hard_threshold: float = 0.95,
    ) -> None:
        self.embedding_provider = embedding_provider
        self.verifier = verifier or AdkMergeVerifier()
        self.soft_threshold = soft_threshold
        self.hard_threshold = hard_threshold

    @staticmethod
    def extract_entity_display_name(node: dict[str, Any]) -> str:
        """Trích xuất tên hiển thị tốt nhất của node từ identity hoặc properties."""
        identity = node.get("identity") or {}
        for key in ("name", "fullName", "title", "code", "clubName", "orgName"):
            if identity.get(key):
                return str(identity[key])
        for prop in node.get("properties", []):
            if prop.get("propertyName") in ("name", "fullName", "title", "nameVi"):
                val = prop.get("value")
                if val:
                    return str(val)
        return str(node.get("tempId", "Unnamed"))

    @classmethod
    def node_text_representation(cls, node: dict[str, Any]) -> str:
        """Tạo chuỗi văn bản đại diện cho node để tạo vector embedding."""
        parts = [f"Class: {node.get('className', '')}"]
        name = cls.extract_entity_display_name(node)
        if name:
            parts.append(f"Name: {name}")
        identity = node.get("identity") or {}
        if identity:
            parts.append(f"Identity: {json.dumps(identity, ensure_ascii=False)}")
        for prop in node.get("properties", []):
            p_name = prop.get("propertyName")
            p_val = prop.get("value")
            if p_name and p_val not in (None, "", [], {}):
                parts.append(f"{p_name}: {p_val}")
        return " | ".join(parts)

    async def detect_disambiguations(
        self,
        nodes: list[dict[str, Any]],
    ) -> list[DisambiguationPair]:
        """Phát hiện các cặp thực thể có độ tương đồng cao trong danh sách nodes."""
        if not self.embedding_provider or len(nodes) < 2:
            return []

        # Gom nhóm node theo className
        nodes_by_class: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
            c_name = node.get("className", "")
            if c_name:
                nodes_by_class.setdefault(c_name, []).append(node)

        pairs: list[DisambiguationPair] = []

        for class_name, class_nodes in nodes_by_class.items():
            if len(class_nodes) < 2:
                continue

            texts = [self.node_text_representation(n) for n in class_nodes]
            vectors = await self.embedding_provider.embed_documents(texts)

            for i in range(len(class_nodes)):
                for j in range(i + 1, len(class_nodes)):
                    node_a = class_nodes[i]
                    node_b = class_nodes[j]
                    score = cosine_similarity(vectors[i], vectors[j])

                    if score >= self.soft_threshold:
                        name_a = self.extract_entity_display_name(node_a)
                        name_b = self.extract_entity_display_name(node_b)

                        # Nếu 2 node đã có cùng tên 100%, bỏ qua vì đã được deterministic merge xử lý
                        if name_a.strip().lower() == name_b.strip().lower():
                            continue

                        # Kiểm tra với LLM Verifier
                        is_merge, reason = self.verifier.verify(
                            class_name=class_name,
                            entity_a=texts[i],
                            entity_b=texts[j],
                            score=score,
                        )

                        pair_id = f"disambig_{node_a['tempId']}_{node_b['tempId']}"
                        rec_action = "MERGE" if (score >= self.hard_threshold or is_merge) else "DISTINCT"

                        pairs.append(
                            DisambiguationPair(
                                pair_id=pair_id,
                                class_name=class_name,
                                source_temp_id=node_a["tempId"],
                                source_name=name_a,
                                target_temp_id=node_b["tempId"],
                                target_name=name_b,
                                similarity=score,
                                reason=reason,
                                recommended_action=rec_action,
                            )
                        )

        return pairs
