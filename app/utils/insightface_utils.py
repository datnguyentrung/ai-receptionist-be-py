import logging
import os
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from typing import Any

dll_directory_handles: list[object] = []


def _configure_windows_cuda_dll_search_path() -> None:
    """Make CUDA/cuDNN DLLs installed in a Conda environment discoverable."""

    if os.name != "nt":
        return

    conda_library_bin = os.path.join(sys.prefix, "Library", "bin")
    if os.path.isdir(conda_library_bin):
        dll_directory_handles.append(os.add_dll_directory(conda_library_bin))


_configure_windows_cuda_dll_search_path()

import onnxruntime as ort  # noqa: E402 - CUDA DLL search path must be configured first.

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

    started_at = time.perf_counter()
    os.makedirs(MODEL_DIR, exist_ok=True)

    providers = settings.insightface_providers
    available_providers = ort.get_available_providers()
    unavailable_providers = set(providers) - set(available_providers)
    if unavailable_providers:
        raise RuntimeError(
            "Requested ONNX Runtime provider(s) are unavailable: "
            f"{', '.join(sorted(unavailable_providers))}. "
            f"Available: {', '.join(available_providers)}"
        )

    # ctx_id=0: GPU đầu tiên; ctx_id=-1: CPU
    ctx_id = 0 if "CUDAExecutionProvider" in providers else -1

    logger.info(
        "insightface_initialization_started | model=%s | providers=%s | root=%s",
        settings.INSIGHTFACE_MODEL_NAME,
        providers,
        MODEL_DIR,
    )

    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        app = FaceAnalysis(
            name=settings.INSIGHTFACE_MODEL_NAME,
            root=MODEL_DIR,
            providers=providers,
        )
        app.prepare(ctx_id=ctx_id, det_size=(640, 640))

    if "CUDAExecutionProvider" in providers:
        active_providers = {
            provider
            for model in app.models.values()
            for provider in model.session.get_providers()
        }
        if "CUDAExecutionProvider" not in active_providers:
            raise RuntimeError(
                "CUDAExecutionProvider was requested but InsightFace fell back to CPU. "
                f"Active providers: {', '.join(sorted(active_providers))}"
            )

    face_app = app

    logger.info(
        "insightface_initialization_completed | model=%s | providers=%s | durationMs=%.2f",
        settings.INSIGHTFACE_MODEL_NAME,
        providers,
        (time.perf_counter() - started_at) * 1000,
    )


def is_face_app_initialized() -> bool:
    return face_app is not None


def detect_faces(img_array: Any, request_id: str | None = None):
    if face_app is None:
        raise RuntimeError("InsightFace model is not initialized")

    started_at = time.perf_counter()
    faces = face_app.get(img_array)
    duration_ms = (time.perf_counter() - started_at) * 1000
    logger.info(
        "insightface_detection_completed | requestId=%s | faceCount=%s | durationMs=%.2f",
        request_id,
        len(faces),
        duration_ms,
    )
    return faces
