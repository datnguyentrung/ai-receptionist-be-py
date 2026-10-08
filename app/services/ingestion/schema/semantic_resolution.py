"""Module giải quyết và khử nhập nhằng thực thể ngữ nghĩa (Semantic Entity Resolution & Disambiguation).

Module này cung cấp cơ chế so khớp ngữ nghĩa thực thể dựa trên:
1. Vector Embedding Cosine Similarity (GeminiEmbeddingProvider).
2. Xác minh ngữ cảnh bằng mô hình LLM (AdkMergeVerifier).
3. Phân luồng quyết định 3 cấp độ:
   - AUTO_MERGE (độ tương đồng >= 0.95 hoặc LLM xác nhận là một).
   - AMBIGUOUS (0.75 <= độ tương đồng < 0.95 -> chờ đánh giá / Human-in-the-loop).
   - DISTINCT (độ tương đồng < 0.75 -> hai thực thể độc lập).

Danh sách các hàm / phương thức trong module:
- `SemanticCandidate`: Mô hình dữ liệu đại diện cho một ứng viên thực thể ngữ nghĩa.
- `DisambiguationPair`: Mô hình dữ liệu đại diện cho một cặp thực thể cần khử nhập nhằng.
- `AdkMergeVerifier.__init__(...)`: Khởi tạo LLM Verifier với cấu hình model và API key của Gemini.
- `AdkMergeVerifier.verify(...)`: Đánh giá xem hai thực thể có cùng đại diện cho một đối tượng thực tế hay không.
- `SemanticEntityResolver.__init__(...)`: Khởi tạo bộ giải quyết thực thể với embedding provider và verifier.
- `SemanticEntityResolver.extract_entity_display_name(...)`: Trích xuất tên hiển thị tốt nhất của node từ identity hoặc properties.
- `SemanticEntityResolver.node_text_representation(...)`: Tạo chuỗi văn bản đại diện cho node để tính vector embedding.
- `SemanticEntityResolver.detect_disambiguations(...)`: Phát hiện các cặp thực thể có độ tương đồng cao trong danh sách nodes.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types

from app.services.graphrag.embeddings import GeminiEmbeddingProvider, cosine_similarity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SemanticCandidate:
    """
    Cấu trúc dữ liệu đại diện cho một ứng viên thực thể ngữ nghĩa.
    """
    temp_id: str
    class_name: str
    display_name: str
    identity: dict[str, Any]
    properties: dict[str, Any]
    score: float


@dataclass
class DisambiguationPair:
    """
    Cấu trúc dữ liệu chứa thông tin về một cặp thực thể có độ tương đồng cao cần khử nhập nhằng.
    """
    pair_id: str
    class_name: str
    source_temp_id: str
    source_name: str
    target_temp_id: str
    target_name: str
    similarity: float
    reason: str
    recommended_action: str = "MERGE"  # MERGE hoặc DISTINCT


class AdkMergeVerifier:
    """
    Bộ thẩm định sử dụng mô hình LLM Gemini để đánh giá xem hai thực thể có cùng là một đối tượng thực tế không.
    """

    def __init__(self, *, model: str | None = None, api_key: str | None = None) -> None:
        """
        Khởi tạo AdkMergeVerifier.

        Args:
            model: Tên mô hình Gemini (mặc định lấy từ INGESTION_MODEL hoặc GOOGLE_ADK_MODEL).
            api_key: Khóa API của Google Gemini.
        """
        # 1. Xác định tên model Gemini
        self.model = model or os.getenv(
            "INGESTION_MODEL", os.getenv("GOOGLE_ADK_MODEL", "gemini-2.5-flash")
        )
        # 2. Lấy API key từ biến môi trường
        api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        # 3. Khởi tạo client Gemini SDK
        self.client = genai.Client(api_key=api_key) if api_key else genai.Client()

    def verify(
        self,
        *,
        class_name: str,
        entity_a: str,
        entity_b: str,
        score: float,
    ) -> tuple[bool, str]:
        """
        Đánh giá xem hai thực thể có cùng đại diện cho một đối tượng trong thực tế hay không.

        Args:
            class_name: Tên loại thực thể (ví dụ: 'CLB', 'HLV', 'VõSinh').
            entity_a: Văn bản mô tả thực thể thứ nhất.
            entity_b: Văn bản mô tả thực thể thứ hai.
            score: Điểm tương đồng cosine similarity giữa hai vector.

        Returns:
            tuple[bool, str]: Bộ đôi gồm (is_merge: True/False, reason: Giải thích ngắn gọn).
        """
        # 1. Xây dựng prompt hướng dẫn chuyên gia thẩm định thực thể
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
            # 2. Gọi API Gemini với cấu hình JSON schema có cấu trúc
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
            # 3. Phân tích kết quả JSON trả về
            if not response.text:
                return False, "Không nhận được phản hồi từ mô hình."
            data = json.loads(response.text)
            is_merge = data.get("decision") == "MERGE"
            reason = data.get("reason", "")
            return is_merge, reason
        except Exception as exc:
            # Ghi log cảnh báo khi xảy ra lỗi gọi LLM
            logger.warning(
                "[ADK_MERGE_VERIFIER_ERROR] model=%s error=%s", self.model, exc
            )
            return False, f"Lỗi xác minh: {exc}"


class SemanticEntityResolver:
    """
    Bộ giải quyết và phát hiện nhập nhằng thực thể dựa trên độ tương đồng vector và xác minh LLM.
    """

    def __init__(
        self,
        *,
        embedding_provider: GeminiEmbeddingProvider | None = None,
        verifier: AdkMergeVerifier | None = None,
        soft_threshold: float = 0.75,
        hard_threshold: float = 0.95,
    ) -> None:
        """
        Khởi tạo SemanticEntityResolver.

        Args:
            embedding_provider: Bộ tạo vector embedding Gemini.
            verifier: Bộ xác minh LLM (AdkMergeVerifier).
            soft_threshold: Ngưỡng tương đồng mềm để đưa vào danh sách nghi ngờ (mặc định 0.75).
            hard_threshold: Ngưỡng tương đồng cứng để tự động gộp (mặc định 0.95).
        """
        # 1. Lưu embedding provider
        self.embedding_provider = embedding_provider
        # 2. Khởi tạo hoặc gán verifier
        self.verifier = verifier or AdkMergeVerifier()
        # 3. Lưu các ngưỡng tương đồng
        self.soft_threshold = soft_threshold
        self.hard_threshold = hard_threshold

    @staticmethod
    def extract_entity_display_name(node: dict[str, Any]) -> str:
        """
        Trích xuất tên hiển thị tốt nhất của node từ identity hoặc properties.

        Args:
            node: Từ điển chứa thông tin nút đồ thị.

        Returns:
            str: Tên hiển thị của thực thể.
        """
        # 1. Tìm tên trong cấu trúc identity
        identity = node.get("identity") or {}
        for key in ("name", "fullName", "title", "code", "clubName", "orgName"):
            if identity.get(key):
                return str(identity[key])
        # 2. Tìm tên trong danh sách thuộc tính properties
        for prop in node.get("properties", []):
            if prop.get("propertyName") in ("name", "fullName", "title", "nameVi"):
                val = prop.get("value")
                if val:
                    return str(val)
        # 3. Fallback về tempId nếu không tìm thấy tên
        return str(node.get("tempId", "Unnamed"))

    @classmethod
    def node_text_representation(cls, node: dict[str, Any]) -> str:
        """
        Tạo chuỗi văn bản đại diện tổng hợp cho node để tính vector embedding.

        Args:
            node: Từ điển chứa thông tin nút đồ thị.

        Returns:
            str: Chuỗi văn bản đại diện.
        """
        # 1. Thêm thông tin loại thực thể (Class)
        parts = [f"Class: {node.get('className', '')}"]
        # 2. Thêm tên hiển thị
        name = cls.extract_entity_display_name(node)
        if name:
            parts.append(f"Name: {name}")
        # 3. Thêm định danh identity
        identity = node.get("identity") or {}
        if identity:
            parts.append(f"Identity: {json.dumps(identity, ensure_ascii=False)}")
        # 4. Thêm các thuộc tính có giá trị
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
        """
        Phát hiện các cặp thực thể có độ tương đồng cao trong danh sách nodes để khử nhập nhằng.

        Args:
            nodes: Danh sách các nút đồ thị trích xuất được.

        Returns:
            list[DisambiguationPair]: Danh sách các cặp thực thể cần xem xét hợp nhất.
        """
        # 1. Kiểm tra điều kiện có embedding provider và ít nhất 2 nodes
        if not self.embedding_provider or len(nodes) < 2:
            return []

        # 2. Gom nhóm các node theo className để chỉ so khớp các thực thể cùng loại
        nodes_by_class: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
            c_name = node.get("className", "")
            if c_name:
                nodes_by_class.setdefault(c_name, []).append(node)

        pairs: list[DisambiguationPair] = []

        # 3. Duyệt qua từng nhóm className
        for class_name, class_nodes in nodes_by_class.items():
            if len(class_nodes) < 2:
                continue

            # Tạo vector embedding cho tất cả các node trong nhóm
            texts = [self.node_text_representation(n) for n in class_nodes]
            vectors = await self.embedding_provider.embed_documents(texts)

            # So khớp từng cặp node (i, j) trong cùng một class
            for i in range(len(class_nodes)):
                for j in range(i + 1, len(class_nodes)):
                    node_a = class_nodes[i]
                    node_b = class_nodes[j]
                    score = cosine_similarity(vectors[i], vectors[j])

                    # Nếu độ tương đồng vượt ngưỡng soft_threshold
                    if score >= self.soft_threshold:
                        name_a = self.extract_entity_display_name(node_a)
                        name_b = self.extract_entity_display_name(node_b)

                        # Nếu 2 node đã có cùng tên 100%, bỏ qua vì đã được deterministic merge xử lý
                        if name_a.strip().lower() == name_b.strip().lower():
                            continue

                        # Xác minh ngữ cảnh bằng LLM Verifier
                        is_merge, reason = self.verifier.verify(
                            class_name=class_name,
                            entity_a=texts[i],
                            entity_b=texts[j],
                            score=score,
                        )

                        pair_id = f"disambig_{node_a['tempId']}_{node_b['tempId']}"
                        rec_action = (
                            "MERGE"
                            if (score >= self.hard_threshold or is_merge)
                            else "DISTINCT"
                        )

                        # Thêm vào danh sách cặp cần khử nhập nhằng
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


__all__ = [
    "AdkMergeVerifier",
    "DisambiguationPair",
    "SemanticCandidate",
    "SemanticEntityResolver",
]
