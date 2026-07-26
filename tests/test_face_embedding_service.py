import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from app.core.config import settings
from app.enums.face_embedding_error_code import FaceEmbeddingErrorCode
from app.exceptions.face_embedding_exception import FaceEmbeddingException
from app.services.face_embedding_service import FaceEmbeddingService


class FakeUploadFile:
    def __init__(
        self,
        contents: bytes,
        filename: str = "face.jpg",
        content_type: str = "image/jpeg",
    ) -> None:
        self._contents = contents
        self.filename = filename
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._contents


def image_bytes() -> bytes:
    image = np.zeros((16, 16, 3), dtype=np.uint8)
    success, encoded = cv2.imencode(".jpg", image)
    assert success
    return encoded.tobytes()


def embedding(dimension: int | None = None) -> np.ndarray:
    size = dimension or settings.FACE_EMBEDDING_DIMENSION
    return np.linspace(0.0, 1.0, size, dtype=np.float64)


def run(coro):
    return asyncio.run(coro)


def assert_error(exc_info, error_code: FaceEmbeddingErrorCode) -> None:
    assert exc_info.value.error_code == error_code


def test_generate_embedding_success() -> None:
    face = SimpleNamespace(embedding=embedding())
    service = FaceEmbeddingService()

    with (
        patch("app.services.face_embedding_service.is_face_app_initialized", return_value=True),
        patch("app.services.face_embedding_service.detect_faces", return_value=[face]),
    ):
        response = run(service.generate_face_embedding(FakeUploadFile(image_bytes())))

    assert response.success is True
    assert response.dimension == settings.FACE_EMBEDDING_DIMENSION
    assert response.errorCode is None
    assert response.message is None
    assert response.embedding == embedding().tolist()


def test_missing_file_rejected() -> None:
    service = FaceEmbeddingService()

    with pytest.raises(FaceEmbeddingException) as exc_info:
        run(service.generate_face_embedding(None))

    assert_error(exc_info, FaceEmbeddingErrorCode.INVALID_IMAGE_FILE)


def test_empty_file_rejected() -> None:
    service = FaceEmbeddingService()

    with pytest.raises(FaceEmbeddingException) as exc_info:
        run(service.generate_face_embedding(FakeUploadFile(b"")))

    assert_error(exc_info, FaceEmbeddingErrorCode.EMPTY_IMAGE_FILE)


def test_file_too_large_rejected(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MAX_IMAGE_SIZE_BYTES", 1)
    service = FaceEmbeddingService()

    with pytest.raises(FaceEmbeddingException) as exc_info:
        run(service.generate_face_embedding(FakeUploadFile(b"123")))

    assert_error(exc_info, FaceEmbeddingErrorCode.FILE_TOO_LARGE)


def test_unsupported_mime_type_rejected() -> None:
    service = FaceEmbeddingService()

    with pytest.raises(FaceEmbeddingException) as exc_info:
        run(
            service.generate_face_embedding(
                FakeUploadFile(image_bytes(), content_type="text/plain")
            )
        )

    assert_error(exc_info, FaceEmbeddingErrorCode.UNSUPPORTED_IMAGE_TYPE)


def test_fake_image_mime_rejected_when_decode_fails() -> None:
    service = FaceEmbeddingService()

    with pytest.raises(FaceEmbeddingException) as exc_info:
        run(service.generate_face_embedding(FakeUploadFile(b"not an image")))

    assert_error(exc_info, FaceEmbeddingErrorCode.IMAGE_DECODE_FAILED)


def test_corrupt_image_rejected_when_decode_fails() -> None:
    service = FaceEmbeddingService()

    with pytest.raises(FaceEmbeddingException) as exc_info:
        run(service.generate_face_embedding(FakeUploadFile(b"\xff\xd8\xff")))

    assert_error(exc_info, FaceEmbeddingErrorCode.IMAGE_DECODE_FAILED)


def test_no_face_rejected() -> None:
    service = FaceEmbeddingService()

    with (
        patch("app.services.face_embedding_service.is_face_app_initialized", return_value=True),
        patch("app.services.face_embedding_service.detect_faces", return_value=[]),
        pytest.raises(FaceEmbeddingException) as exc_info,
    ):
        run(service.generate_face_embedding(FakeUploadFile(image_bytes())))

    assert_error(exc_info, FaceEmbeddingErrorCode.FACE_NOT_DETECTED)


def test_multiple_faces_rejected() -> None:
    service = FaceEmbeddingService()
    faces = [SimpleNamespace(embedding=embedding()), SimpleNamespace(embedding=embedding())]

    with (
        patch("app.services.face_embedding_service.is_face_app_initialized", return_value=True),
        patch("app.services.face_embedding_service.detect_faces", return_value=faces),
        pytest.raises(FaceEmbeddingException) as exc_info,
    ):
        run(service.generate_face_embedding(FakeUploadFile(image_bytes())))

    assert_error(exc_info, FaceEmbeddingErrorCode.MULTIPLE_FACES_DETECTED)


def test_model_not_initialized_rejected() -> None:
    service = FaceEmbeddingService()

    with (
        patch("app.services.face_embedding_service.is_face_app_initialized", return_value=False),
        pytest.raises(FaceEmbeddingException) as exc_info,
    ):
        run(service.generate_face_embedding(FakeUploadFile(image_bytes())))

    assert_error(exc_info, FaceEmbeddingErrorCode.MODEL_NOT_INITIALIZED)


def test_embedding_missing_rejected() -> None:
    service = FaceEmbeddingService()

    with (
        patch("app.services.face_embedding_service.is_face_app_initialized", return_value=True),
        patch(
            "app.services.face_embedding_service.detect_faces",
            return_value=[SimpleNamespace(embedding=None)],
        ),
        pytest.raises(FaceEmbeddingException) as exc_info,
    ):
        run(service.generate_face_embedding(FakeUploadFile(image_bytes())))

    assert_error(exc_info, FaceEmbeddingErrorCode.FACE_EMBEDDING_FAILED)


def test_embedding_empty_rejected() -> None:
    service = FaceEmbeddingService()

    with pytest.raises(FaceEmbeddingException) as exc_info:
        service._validate_embedding([])

    assert_error(exc_info, FaceEmbeddingErrorCode.INVALID_EMBEDDING)


def test_embedding_wrong_dimension_rejected() -> None:
    service = FaceEmbeddingService()

    with pytest.raises(FaceEmbeddingException) as exc_info:
        service._validate_embedding([0.1])

    assert_error(exc_info, FaceEmbeddingErrorCode.INVALID_EMBEDDING)


def test_embedding_nan_rejected() -> None:
    service = FaceEmbeddingService()
    vector = embedding()
    vector[0] = np.nan

    with pytest.raises(FaceEmbeddingException) as exc_info:
        service._validate_embedding(vector)

    assert_error(exc_info, FaceEmbeddingErrorCode.INVALID_EMBEDDING)


def test_embedding_infinity_rejected() -> None:
    service = FaceEmbeddingService()
    vector = embedding()
    vector[0] = np.inf

    with pytest.raises(FaceEmbeddingException) as exc_info:
        service._validate_embedding(vector)

    assert_error(exc_info, FaceEmbeddingErrorCode.INVALID_EMBEDDING)
