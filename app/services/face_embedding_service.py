import logging
import time

import cv2
import numpy as np
from fastapi import UploadFile

from app.core.config import settings
from app.enums.face_embedding_error_code import FaceEmbeddingErrorCode
from app.exceptions.face_embedding_exception import FaceEmbeddingException
from app.schemas.face_embedding_schema import FaceEmbeddingResponse
from app.utils.insightface_utils import detect_faces, is_face_app_initialized

logger = logging.getLogger(__name__)


class FaceEmbeddingService:
    async def generate_face_embedding(
        self,
        file: UploadFile | None,
        request_id: str | None = None,
    ) -> FaceEmbeddingResponse:
        started_at = time.perf_counter()
        if file is None:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.INVALID_IMAGE_FILE)

        filename = file.filename or ""
        content_type = (file.content_type or "").lower()
        logger.info(
            "request_received | requestId=%s | filename=%s | mimeType=%s",
            request_id,
            filename,
            content_type,
        )

        if not filename:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.INVALID_IMAGE_FILE)

        if content_type not in settings.allowed_image_types:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.UNSUPPORTED_IMAGE_TYPE)

        try:
            contents = await file.read()
        except Exception as exc:
            logger.exception("request_failed | requestId=%s | step=file_read", request_id)
            raise FaceEmbeddingException(
                FaceEmbeddingErrorCode.INVALID_IMAGE_FILE
            ) from exc

        size_bytes = len(contents)
        logger.info(
            "file_read_completed | requestId=%s | filename=%s | mimeType=%s | sizeBytes=%s",
            request_id,
            filename,
            content_type,
            size_bytes,
        )

        if size_bytes == 0:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.EMPTY_IMAGE_FILE)

        if size_bytes > settings.MAX_IMAGE_SIZE_BYTES:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.FILE_TOO_LARGE)

        image_np = self._decode_image(contents, request_id)

        if not is_face_app_initialized():
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.MODEL_NOT_INITIALIZED)

        try:
            faces = detect_faces(image_np, request_id=request_id)
        except FaceEmbeddingException:
            raise
        except Exception as exc:
            logger.exception(
                "request_failed | requestId=%s | step=face_detection", request_id
            )
            raise FaceEmbeddingException(
                FaceEmbeddingErrorCode.FACE_EMBEDDING_FAILED
            ) from exc

        face_count = len(faces)
        logger.info(
            "face_detection_completed | requestId=%s | faceCount=%s",
            request_id,
            face_count,
        )

        if face_count == 0:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.FACE_NOT_DETECTED)
        if face_count > 1:
            raise FaceEmbeddingException(
                FaceEmbeddingErrorCode.MULTIPLE_FACES_DETECTED
            )

        embedding = getattr(faces[0], "embedding", None)
        if embedding is None:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.FACE_EMBEDDING_FAILED)

        embedding_list = self._validate_embedding(embedding)
        dimension = len(embedding_list)
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "embedding_generated | requestId=%s | dimension=%s",
            request_id,
            dimension,
        )
        logger.info(
            "response_completed | requestId=%s | filename=%s | mimeType=%s | "
            "sizeBytes=%s | imageShape=%s | faceCount=%s | dimension=%s | "
            "durationMs=%.2f",
            request_id,
            filename,
            content_type,
            size_bytes,
            tuple(image_np.shape),
            face_count,
            dimension,
            duration_ms,
        )

        return FaceEmbeddingResponse(
            success=True,
            embedding=embedding_list,
            dimension=dimension,
            model=settings.INSIGHTFACE_MODEL_NAME,
            errorCode=None,
            message=None,
        )

    def _decode_image(self, contents: bytes, request_id: str | None) -> np.ndarray:
        try:
            parser = np.frombuffer(contents, np.uint8)
            image_np = cv2.imdecode(parser, cv2.IMREAD_COLOR)
        except Exception as exc:
            logger.exception("request_failed | requestId=%s | step=image_decode", request_id)
            raise FaceEmbeddingException(
                FaceEmbeddingErrorCode.IMAGE_DECODE_FAILED
            ) from exc

        if image_np is None or image_np.ndim < 2:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.IMAGE_DECODE_FAILED)

        height, width = image_np.shape[:2]
        if height <= 0 or width <= 0:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.IMAGE_DECODE_FAILED)

        logger.info(
            "image_decode_completed | requestId=%s | imageWidth=%s | imageHeight=%s",
            request_id,
            width,
            height,
        )
        return image_np

    def _validate_embedding(self, embedding: object) -> list[float]:
        try:
            vector = np.asarray(embedding, dtype=np.float64).reshape(-1)
        except Exception as exc:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.INVALID_EMBEDDING) from exc

        if vector.size == 0:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.INVALID_EMBEDDING)

        if vector.size != settings.FACE_EMBEDDING_DIMENSION:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.INVALID_EMBEDDING)

        if not np.isfinite(vector).all():
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.INVALID_EMBEDDING)

        return [float(value) for value in vector.tolist()]
