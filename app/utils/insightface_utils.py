import logging
import os
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from typing import Any

os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")

from insightface.app import FaceAnalysis

from app.core.config import settings

MODEL_DIR = os.path.abspath(
    os.environ.get(
        "INSIGHTFACE_HOME",
        os.path.join(os.path.dirname(__file__), "..", "..", "insightface_data"),
    )
)
os.environ["INSIGHTFACE_HOME"] = MODEL_DIR

face_app: FaceAnalysis | None = None
logger = logging.getLogger(__name__)


def initialize_face_app() -> None:
    global face_app
    os.makedirs(MODEL_DIR, exist_ok=True)
    logger.info(
        "insightface_initialization_started | model=%s | providers=%s | root=%s",
        settings.INSIGHTFACE_MODEL_NAME,
        settings.insightface_providers,
        MODEL_DIR,
    )
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        app = FaceAnalysis(
            name=settings.INSIGHTFACE_MODEL_NAME,
            root=MODEL_DIR,
            providers=settings.insightface_providers,
        )
        app.prepare(ctx_id=-1, det_size=(640, 640))
    face_app = app
    logger.info(
        "insightface_initialization_completed | model=%s",
        settings.INSIGHTFACE_MODEL_NAME,
    )


def is_face_app_initialized() -> bool:
    return face_app is not None


def detect_faces(img_array: Any, request_id: str | None = None):
    if face_app is None:
        raise RuntimeError("InsightFace model is not initialized")

    faces = face_app.get(img_array)
    logger.info(
        "insightface_detection_completed | requestId=%s | faceCount=%s",
        request_id,
        len(faces),
    )
    return faces
