import logging
import os
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO

import numpy as np

os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")

from insightface.app import FaceAnalysis

from app.exceptions.face_check_in_exception import (
    FaceCheckInErrorCode,
    FaceCheckInException,
)

# Đồng bộ INSIGHTFACE_HOME với Docker build (download_model.py)
MODEL_DIR = os.path.abspath(
    os.environ.get(
        "INSIGHTFACE_HOME",
        os.path.join(os.path.dirname(__file__), "..", "..", "insightface_data"),
    )
)
os.environ["INSIGHTFACE_HOME"] = MODEL_DIR
face_app = None
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FaceEmbeddingResult:
    embedding: list[float] | None
    face_count: int


def initialize_cpu_face_app():
    """Khởi tạo model nhận diện khuôn mặt tối ưu cho CPU"""
    os.makedirs(MODEL_DIR, exist_ok=True)
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        app = FaceAnalysis(
            name="buffalo_s",
            root=MODEL_DIR,
            providers=["CPUExecutionProvider"],
        )
        app.prepare(ctx_id=-1, det_size=(640, 640))
    logger.info("InsightFace CPU initialized successfully (model=buffalo_s)")
    return app


def ensure_face_app_initialized():
    global face_app
    if face_app is None:
        try:
            face_app = initialize_cpu_face_app()
        except Exception as e:
            raise RuntimeError(f"InsightFace initialization error: {e!r}") from e
    return face_app


def get_face_embedding(
    img_array: np.ndarray, request_id: str | None = None
) -> list[float] | None:
    """
    Trích xuất vector khuôn mặt to nhất trong ảnh.
    Trả về list 512 phần tử (float) để lưu vào pgvector.
    """
    try:
        app = ensure_face_app_initialized()
        faces = app.get(img_array)
        logger.info(
            "CHECK_IN_STEP insightface_completed | request_id=%s | face_count=%s",
            request_id,
            len(faces),
        )
        if not faces:
            return None  # Không tìm thấy ai

        # Chọn khuôn mặt có diện tích lớn nhất (người đứng gần màn hình lễ tân nhất)
        largest_face = max(
            faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])
        )

        return largest_face.embedding.tolist()

    except Exception as e:
        logger.exception(
            "CHECK_IN_ERROR face_embedding_extraction_failed | request_id=%s",
            request_id,
        )
        raise RuntimeError(f"Face embedding extraction error: {e!r}") from e


def get_single_face_embedding(
    img_array: np.ndarray, request_id: str | None = None
) -> FaceEmbeddingResult:
    try:
        app = ensure_face_app_initialized()
        faces = app.get(img_array)
        face_count = len(faces)
        logger.info(
            "CHECK_IN_STEP insightface_completed | request_id=%s | face_count=%s",
            request_id,
            face_count,
        )
        if face_count == 0:
            return FaceEmbeddingResult(embedding=None, face_count=0)

        if face_count > 1:
            raise FaceCheckInException(
                FaceCheckInErrorCode.MULTIPLE_FACES_DETECTED,
                detail_message=f"Detected {face_count} faces",
            )

        return FaceEmbeddingResult(
            embedding=faces[0].embedding.tolist(),
            face_count=face_count,
        )

    except FaceCheckInException:
        raise
    except Exception as e:
        logger.exception(
            "CHECK_IN_ERROR face_embedding_extraction_failed | request_id=%s",
            request_id,
        )
        raise RuntimeError(f"Face embedding extraction error: {e!r}") from e
