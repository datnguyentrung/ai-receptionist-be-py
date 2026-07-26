from pydantic import BaseModel, Field

from app.core.config import settings
from app.enums.face_embedding_error_code import FaceEmbeddingErrorCode


class FaceEmbeddingResponse(BaseModel):
    success: bool = Field(
        examples=[True],
        description="True when an embedding was generated successfully.",
    )
    embedding: list[float] | None = Field(
        default=None,
        description="Face embedding vector. Null for error responses.",
    )
    dimension: int | None = Field(
        default=None,
        examples=[512],
        description="Actual embedding vector dimension. Null for errors.",
    )
    model: str = Field(
        default_factory=lambda: settings.INSIGHTFACE_MODEL_NAME,
        examples=["buffalo_l"],
        description="InsightFace model used by the service.",
    )
    errorCode: FaceEmbeddingErrorCode | None = Field(
        default=None,
        examples=[FaceEmbeddingErrorCode.FACE_NOT_DETECTED],
        description="Stable service error code. Null for successful responses.",
    )
    message: str | None = Field(
        default=None,
        examples=["Không phát hiện được khuôn mặt trong ảnh"],
        description="Human-readable error message. Null for successful responses.",
    )
