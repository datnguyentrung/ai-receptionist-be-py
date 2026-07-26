from fastapi.testclient import TestClient

from app.enums.face_embedding_error_code import FaceEmbeddingErrorCode
from app.main import app
from app.schemas.face_embedding_schema import FaceEmbeddingResponse

client = TestClient(app, raise_server_exceptions=False)


def test_face_embedding_endpoint_rejects_missing_file() -> None:
    response = client.post("/face-embeddings")

    assert response.status_code == 400
    assert response.json()["success"] is False
    assert response.json()["errorCode"] == FaceEmbeddingErrorCode.INVALID_IMAGE_FILE


def test_face_embedding_endpoint_returns_success_contract(monkeypatch) -> None:
    async def fake_generate(self, file, request_id=None):
        return FaceEmbeddingResponse(
            success=True,
            embedding=[0.1, 0.2],
            dimension=2,
            model="buffalo_l",
            errorCode=None,
            message=None,
        )

    monkeypatch.setattr(
        "app.api.face_embedding_api.FaceEmbeddingService.generate_face_embedding",
        fake_generate,
    )

    response = client.post(
        "/face-embeddings",
        files={"file": ("face.jpg", b"image-bytes", "image/jpeg")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "embedding": [0.1, 0.2],
        "dimension": 2,
        "model": "buffalo_l",
        "errorCode": None,
        "message": None,
    }


def test_face_embedding_endpoint_returns_internal_error_contract(monkeypatch) -> None:
    async def fake_generate(self, file, request_id=None):
        raise ValueError("sensitive internal detail")

    monkeypatch.setattr(
        "app.api.face_embedding_api.FaceEmbeddingService.generate_face_embedding",
        fake_generate,
    )

    response = client.post(
        "/face-embeddings",
        files={"file": ("face.jpg", b"image-bytes", "image/jpeg")},
    )

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["embedding"] is None
    assert body["dimension"] is None
    assert body["errorCode"] == FaceEmbeddingErrorCode.INTERNAL_ERROR
    assert "sensitive internal detail" not in body["message"]
