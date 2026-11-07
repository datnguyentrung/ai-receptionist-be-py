from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.enums.face_embedding_error_code import FaceEmbeddingErrorCode
from app.exceptions.face_embedding_exception import FaceEmbeddingException
from app.services.face_check_in_orchestrator import FaceCheckInOrchestrator


class RecordingJavaClient:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def validate_upload(self, request_id, device_id, upload_ticket):
        self.calls.append(("validate", request_id, device_id, upload_ticket))

    async def report_stage(self, request_id, device_id, stage, error=None):
        self.calls.append(("stage", request_id, device_id, stage, error))

    async def submit_embedding(self, request_id, device_id, embedding):
        self.calls.append(("embedding", request_id, device_id, embedding))


class SuccessfulEmbeddingService:
    async def generate_face_embedding(
        self, file, request_id=None, on_face_detected=None
    ):
        assert on_face_detected is not None
        await on_face_detected()
        return SimpleNamespace(embedding=[0.125] * 512, dimension=512)


class RejectingEmbeddingService:
    async def generate_face_embedding(
        self, file, request_id=None, on_face_detected=None
    ):
        raise FaceEmbeddingException(FaceEmbeddingErrorCode.FACE_NOT_DETECTED)


@pytest.mark.asyncio
async def test_scan_reports_real_stages_then_submits_embedding() -> None:
    java = RecordingJavaClient()
    request_id = uuid4()
    orchestrator = FaceCheckInOrchestrator(java, SuccessfulEmbeddingService())

    await orchestrator.process(
        file=object(),
        request_id=request_id,
        device_id="kiosk-01",
        upload_ticket="opaque-ticket",
    )

    assert [call[0] if call[0] != "stage" else call[3] for call in java.calls] == [
        "validate",
        "IMAGE_RECEIVED",
        "FACE_DETECTED",
        "EMBEDDING_READY",
        "embedding",
    ]
    assert len(java.calls[-1][3]) == 512


@pytest.mark.asyncio
async def test_invalid_face_reports_terminal_rejection() -> None:
    java = RecordingJavaClient()
    request_id = uuid4()
    orchestrator = FaceCheckInOrchestrator(java, RejectingEmbeddingService())

    with pytest.raises(FaceEmbeddingException):
        await orchestrator.process(
            file=object(),
            request_id=request_id,
            device_id="kiosk-01",
            upload_ticket="opaque-ticket",
        )

    terminal = java.calls[-1]
    assert terminal[3] == "REJECTED"
    assert terminal[4]["code"] == "FACE_NOT_DETECTED"
    assert all(call[0] != "embedding" for call in java.calls)
