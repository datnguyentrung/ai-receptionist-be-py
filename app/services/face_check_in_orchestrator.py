from __future__ import annotations

import contextlib
from uuid import UUID

from fastapi import UploadFile

from app.clients.java_attendance_client import JavaAttendanceClient
from app.enums.face_embedding_error_code import FaceEmbeddingErrorCode
from app.exceptions.face_embedding_exception import FaceEmbeddingException
from app.services.face_embedding_service import FaceEmbeddingService


class FaceCheckInOrchestrator:
    """Own one scan from validated upload through Java embedding handoff."""

    def __init__(
        self,
        java_client: JavaAttendanceClient,
        embedding_service: FaceEmbeddingService | None = None,
    ) -> None:
        self._java = java_client
        self._embedding_service = embedding_service or FaceEmbeddingService()

    async def process(
        self,
        *,
        file: UploadFile,
        request_id: UUID,
        device_id: str,
        upload_ticket: str,
    ) -> None:
        # The image is not read until Java confirms the opaque ticket and device.
        await self._java.validate_upload(request_id, device_id, upload_ticket)
        await self._java.report_stage(request_id, device_id, "IMAGE_RECEIVED")

        async def face_detected() -> None:
            await self._java.report_stage(request_id, device_id, "FACE_DETECTED")

        try:
            response = await self._embedding_service.generate_face_embedding(
                file=file,
                request_id=str(request_id),
                on_face_detected=face_detected,
            )
            if response.embedding is None or response.dimension != 512:
                raise FaceEmbeddingException(
                    FaceEmbeddingErrorCode.INVALID_EMBEDDING
                )
            await self._java.report_stage(request_id, device_id, "EMBEDDING_READY")
            await self._java.submit_embedding(
                request_id, device_id, response.embedding
            )
        except FaceEmbeddingException as exc:
            await self._report_face_failure(request_id, device_id, exc)
            raise

    async def _report_face_failure(
        self,
        request_id: UUID,
        device_id: str,
        exc: FaceEmbeddingException,
    ) -> None:
        rejected_codes = {
            FaceEmbeddingErrorCode.INVALID_IMAGE_FILE,
            FaceEmbeddingErrorCode.EMPTY_IMAGE_FILE,
            FaceEmbeddingErrorCode.FILE_TOO_LARGE,
            FaceEmbeddingErrorCode.UNSUPPORTED_IMAGE_TYPE,
            FaceEmbeddingErrorCode.IMAGE_DECODE_FAILED,
            FaceEmbeddingErrorCode.FACE_NOT_DETECTED,
            FaceEmbeddingErrorCode.MULTIPLE_FACES_DETECTED,
        }
        stage = "REJECTED" if exc.error_code in rejected_codes else "FAILED"
        error = {
            "code": exc.error_code.value,
            "title": "Face scan rejected" if stage == "REJECTED" else "Face scan failed",
            "detail": exc.error_code.message,
        }
        # Preserve the local face error if Java becomes unavailable while reporting it.
        with contextlib.suppress(Exception):
            await self._java.report_stage(
                request_id, device_id, stage, error=error
            )
