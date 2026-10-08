import logging
import time
from typing import Any

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

        step_start = time.perf_counter()
        try:
            contents = await file.read()
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.exception(
                "request_failed | requestId=%s | step=file_read | durationMs=%.2f",
                request_id,
                duration_ms,
            )
            raise FaceEmbeddingException(
                FaceEmbeddingErrorCode.INVALID_IMAGE_FILE
            ) from exc

        file_read_ms = (time.perf_counter() - step_start) * 1000
        size_bytes = len(contents)
        logger.info(
            "file_read_completed | requestId=%s | filename=%s | mimeType=%s | sizeBytes=%s | durationMs=%.2f",
            request_id,
            filename,
            content_type,
            size_bytes,
            file_read_ms,
        )

        if size_bytes == 0:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "request_rejected | requestId=%s | reason=empty_file | durationMs=%.2f",
                request_id,
                duration_ms,
            )
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.EMPTY_IMAGE_FILE)

        if size_bytes > settings.MAX_IMAGE_SIZE_BYTES:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "request_rejected | requestId=%s | reason=file_too_large | sizeBytes=%s | durationMs=%.2f",
                request_id,
                size_bytes,
                duration_ms,
            )
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.FILE_TOO_LARGE)

        step_start = time.perf_counter()
        image_np = self._decode_image(contents, request_id, started_at)
        decode_ms = (time.perf_counter() - step_start) * 1000

        if not is_face_app_initialized():
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.error(
                "model_not_initialized | requestId=%s | durationMs=%.2f",
                request_id,
                duration_ms,
            )
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.MODEL_NOT_INITIALIZED)

        step_start = time.perf_counter()
        try:
            faces = detect_faces(image_np, request_id=request_id)
        except FaceEmbeddingException:
            raise
        except Exception as exc:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.exception(
                "request_failed | requestId=%s | step=face_detection | durationMs=%.2f",
                request_id,
                duration_ms,
            )
            raise FaceEmbeddingException(
                FaceEmbeddingErrorCode.FACE_EMBEDDING_FAILED
            ) from exc

        detect_ms = (time.perf_counter() - step_start) * 1000
        face_count = len(faces)
        logger.info(
            "face_detection_completed | requestId=%s | faceCount=%s | durationMs=%.2f",
            request_id,
            face_count,
            detect_ms,
        )

        if face_count == 0:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "request_rejected | requestId=%s | reason=no_face_detected | durationMs=%.2f",
                request_id,
                duration_ms,
            )
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.FACE_NOT_DETECTED)
        if face_count > 1:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.warning(
                "request_rejected | requestId=%s | reason=multiple_faces_detected | faceCount=%s | durationMs=%.2f",
                request_id,
                face_count,
                duration_ms,
            )
            raise FaceEmbeddingException(
                FaceEmbeddingErrorCode.MULTIPLE_FACES_DETECTED
            )

        step_start = time.perf_counter()
        embedding = getattr(faces[0], "embedding", None)
        if embedding is None:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.error(
                "embedding_missing | requestId=%s | durationMs=%.2f",
                request_id,
                duration_ms,
            )
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.FACE_EMBEDDING_FAILED)

        embedding_list = self._validate_embedding(embedding)
        validate_ms = (time.perf_counter() - step_start) * 1000
        dimension = len(embedding_list)
        total_duration_ms = (time.perf_counter() - started_at) * 1000

        logger.info(
            "embedding_generated | requestId=%s | dimension=%s | validateDurationMs=%.2f",
            request_id,
            dimension,
            validate_ms,
        )
        logger.info(
            "response_completed | requestId=%s | filename=%s | mimeType=%s | "
            "sizeBytes=%s | imageShape=%s | faceCount=%s | dimension=%s | "
            "timing={readMs=%.2f, decodeMs=%.2f, detectMs=%.2f, validateMs=%.2f, totalMs=%.2f}",
            request_id,
            filename,
            content_type,
            size_bytes,
            tuple(image_np.shape),
            face_count,
            dimension,
            file_read_ms,
            decode_ms,
            detect_ms,
            validate_ms,
            total_duration_ms,
        )

        return FaceEmbeddingResponse(
            success=True,
            embedding=embedding_list,
            dimension=dimension,
            model=settings.INSIGHTFACE_MODEL_NAME,
            errorCode=None,
            message=None,
        )

    def _decode_image(
        self,
        contents: bytes,
        request_id: str | None,
        started_at: float | None = None,
    ) -> np.ndarray:
        step_start = time.perf_counter()
        try:
            parser = np.frombuffer(contents, np.uint8)
            image_np: Any = cv2.imdecode(parser, cv2.IMREAD_COLOR)
        except Exception as exc:
            duration_ms = (
                (time.perf_counter() - started_at) * 1000
                if started_at is not None
                else (time.perf_counter() - step_start) * 1000
            )
            logger.exception(
                "request_failed | requestId=%s | step=image_decode | durationMs=%.2f",
                request_id,
                duration_ms,
            )
            raise FaceEmbeddingException(
                FaceEmbeddingErrorCode.IMAGE_DECODE_FAILED
            ) from exc

        if image_np is None or image_np.ndim < 2:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.IMAGE_DECODE_FAILED)

        height, width = image_np.shape[:2]
        if height <= 0 or width <= 0:
            raise FaceEmbeddingException(FaceEmbeddingErrorCode.IMAGE_DECODE_FAILED)

        decode_ms = (time.perf_counter() - step_start) * 1000
        logger.info(
            "image_decode_completed | requestId=%s | imageWidth=%s | imageHeight=%s | durationMs=%.2f",
            request_id,
            width,
            height,
            decode_ms,
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
