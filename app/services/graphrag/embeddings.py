"""Dịch vụ tạo Vector Embeddings bằng Google Gemini API cho GraphRAG và Ingestion.

Mô-đun này cung cấp:
- `EmbeddingError`: Lớp ngoại lệ xử lý sự cố trong quá trình tạo embedding hoặc chuẩn hóa vector.
- `GeminiEmbeddingProvider`: Lớp cung cấp embedding bất đồng bộ theo batch cho tài liệu (`RETRIEVAL_DOCUMENT`) và câu truy vấn (`RETRIEVAL_QUERY`).
- `cosine_similarity`: Hàm tính toán độ tương đồng Cosine giữa hai vector.
- `_normalize`: Hàm phụ trợ chuẩn hóa vector về độ dài đơn vị (L2 norm).
"""

import math
from collections.abc import Sequence

from google import genai
from google.genai import types


class EmbeddingError(RuntimeError):
    """Ngoại lệ xảy ra khi có lỗi trong quá trình tạo hoặc xử lý vector embedding."""


class GeminiEmbeddingProvider:
    """Lớp xử lý việc tạo vector embeddings thông qua Google Gemini API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-embedding-001",
        dimension: int = 768,
        batch_size: int = 64,
    ) -> None:
        """Khởi tạo GeminiEmbeddingProvider.

        Tham số:
            api_key: Khóa API của Google AI (Gemini).
            model: Tên model Gemini Embedding sử dụng (mặc định: "gemini-embedding-001").
            dimension: Số chiều của vector embedding kết quả (mặc định: 768).
            batch_size: Số lượng đoạn văn bản được gửi đi trong mỗi batch (mặc định: 64).

        Ném ra:
            EmbeddingError: Nếu api_key bị trống hoặc không hợp lệ.
            ValueError: Nếu số chiều `dimension` không phải là số dương.
        """
        if not api_key.strip():
            raise EmbeddingError(
                "Yêu cầu GOOGLE_API_KEY hoặc GEMINI_API_KEY để sử dụng GraphRAG Embeddings"
            )
        if dimension <= 0:
            raise ValueError("Số chiều embedding (dimension) phải là số dương")
        self.model = model
        self.dimension = dimension
        self.batch_size = max(1, batch_size)
        self._client = genai.Client(api_key=api_key)

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Tạo vector embeddings cho danh sách các tài liệu / đoạn văn bản (chunks).

        Sử dụng task_type="RETRIEVAL_DOCUMENT" để tối ưu cho việc lưu trữ và lập chỉ mục tìm kiếm.

        Tham số:
            texts: Danh sách các chuỗi văn bản cần nhúng vector.

        Trả về:
            Danh sách các vector số thực (đã được chuẩn hóa L2).
        """
        return await self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

    async def embed_query(self, text: str) -> list[float]:
        """Tạo vector embedding cho một câu truy vấn tìm kiếm của người dùng.

        Sử dụng task_type="RETRIEVAL_QUERY" để tối ưu cho việc so khớp tương đồng ngữ nghĩa.

        Tham số:
            text: Câu truy vấn cần tạo embedding.

        Trả về:
            Vector số thực đại diện cho câu truy vấn (đã được chuẩn hóa L2).
        """
        vectors = await self._embed([text], task_type="RETRIEVAL_QUERY")
        return vectors[0]

    async def _embed(
        self, texts: Sequence[str], *, task_type: str
    ) -> list[list[float]]:
        """Hàm nội bộ xử lý gửi yêu cầu tạo embedding theo từng batch đến Gemini API.

        Tham số:
            texts: Danh sách các chuỗi văn bản đầu vào.
            task_type: Loại tác vụ embedding ("RETRIEVAL_DOCUMENT" hoặc "RETRIEVAL_QUERY").

        Trả về:
            Danh sách các vector embedding sau khi kiểm tra số chiều và chuẩn hóa.

        Ném ra:
            EmbeddingError: Khi đầu vào trống, gọi API thất bại hoặc kích thước vector không khớp.
        """
        # Làm sạch và loại bỏ khoảng trắng thừa
        cleaned = [text.strip() for text in texts]
        if any(not text for text in cleaned):
            raise EmbeddingError("Đầu vào tạo embedding không được chứa chuỗi rỗng")

        result: list[list[float]] = []

        # Chia nhỏ danh sách thành các batch để tránh vượt quá giới hạn payload của API
        for start in range(0, len(cleaned), self.batch_size):
            batch = cleaned[start : start + self.batch_size]
            try:
                response = await self._client.aio.models.embed_content(
                    model=self.model,
                    contents=batch,
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        output_dimensionality=self.dimension,
                    ),
                )
            except Exception as exc:
                raise EmbeddingError(
                    f"Tạo embedding cho tác vụ {task_type} thất bại ({type(exc).__name__}): {exc}"
                ) from exc

            embeddings = response.embeddings or []
            if len(embeddings) != len(batch):
                raise EmbeddingError(
                    f"Số lượng embedding trả về không khớp: kỳ vọng {len(batch)}, nhận được {len(embeddings)}"
                )

            # Kiểm tra số chiều và chuẩn hóa từng vector
            for embedding in embeddings:
                vector = [float(value) for value in (embedding.values or [])]
                if len(vector) != self.dimension:
                    raise EmbeddingError(
                        f"Số chiều embedding không khớp: kỳ vọng {self.dimension}, nhận được {len(vector)}"
                    )
                result.append(_normalize(vector))
        return result


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Tính độ tương đồng Cosine (Cosine Similarity) giữa 2 vector số thực.

    Công thức: (A . B) / (||A|| * ||B||)
    Giá trị trả về nằm trong khoảng [-1.0, 1.0], càng gần 1.0 thể hiện mức độ tương đồng ngữ nghĩa càng cao.

    Tham số:
        left: Vector thứ nhất.
        right: Vector thứ hai.

    Trả về:
        Điểm số tương đồng Cosine dưới dạng float. Trả về 0.0 nếu vector rỗng hoặc độ dài không bằng nhau.
    """
    if not left or len(left) != len(right):
        return 0.0
    return float(sum(a * b for a, b in zip(left, right, strict=True))) / (
        math.sqrt(sum(value * value for value in left))
        * math.sqrt(sum(value * value for value in right))
        or 1.0
    )


def _normalize(vector: list[float]) -> list[float]:
    """Chuẩn hóa vector theo chuẩn L2 (Euclidean norm) để đưa độ dài vector về 1.

    Tham số:
        vector: Vector đầu vào cần chuẩn hóa.

    Trả về:
        Vector mới có cùng hướng nhưng độ dài chuẩn hóa bằng 1.

    Ném ra:
        EmbeddingError: Nếu vector có độ dài (norm) bằng 0.
    """
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise EmbeddingError(
            "Vector embedding có độ dài chuẩn (norm) bằng 0, không thể chuẩn hóa"
        )
    return [value / norm for value in vector]


__all__ = ["EmbeddingError", "GeminiEmbeddingProvider", "cosine_similarity"]
