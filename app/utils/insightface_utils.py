import logging
import os
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

import numpy as np

os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")

from insightface.app import FaceAnalysis

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


def get_face_embedding(img_array: np.ndarray) -> list[float] | None:
    """
    Trích xuất vector khuôn mặt to nhất trong ảnh.
    Trả về list 512 phần tử (float) để lưu vào pgvector.
    """
    try:
        app = ensure_face_app_initialized()
        faces = app.get(img_array)
        if not faces:
            return None  # Không tìm thấy ai

        # Chọn khuôn mặt có diện tích lớn nhất (người đứng gần màn hình lễ tân nhất)
        largest_face = max(
            faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])
        )

        return largest_face.embedding.tolist()

    except Exception as e:
        raise RuntimeError(f"Face embedding extraction error: {e!r}") from e
