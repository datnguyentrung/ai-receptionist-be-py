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
from app.utils.insightface_provider import resolve_execution_provider

MODEL_DIR = os.path.abspath(
    os.environ.get(
        "INSIGHTFACE_HOME",
        os.path.join(os.path.dirname(__file__), "..", "..", "insightface_data"),
    )
)
os.environ["INSIGHTFACE_HOME"] = MODEL_DIR

face_app: FaceAnalysis | None = None
active_execution_providers: tuple[str, ...] = ()
logger = logging.getLogger(__name__)


def _create_face_app(provider: str) -> FaceAnalysis:
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        app = FaceAnalysis(
            name=settings.INSIGHTFACE_MODEL_NAME,
            root=MODEL_DIR,
            providers=[provider],
        )
        app.prepare(
            ctx_id=0 if provider == "CUDAExecutionProvider" else -1,
            det_size=(640, 640),
        )
    return app


def _app_execution_providers(app: FaceAnalysis) -> tuple[str, ...]:
    return tuple(sorted({
        provider
        for model in app.models.values()
        for provider in model.session.get_providers()
    }))


def initialize_face_app() -> None:
    global active_execution_providers, face_app

    started_at = time.perf_counter()
    os.makedirs(MODEL_DIR, exist_ok=True)

    requested_provider = settings.face_execution_provider
    selected_provider = resolve_execution_provider(
        requested_provider,
        available_providers=ort.get_available_providers(),
    )

    logger.info(
        "insightface_initialization_started | model=%s | requestedProvider=%s | selectedProvider=%s | root=%s",
        settings.INSIGHTFACE_MODEL_NAME,
        requested_provider,
        selected_provider,
        MODEL_DIR,
    )

    try:
        app = _create_face_app(selected_provider)
        active_execution_providers = _app_execution_providers(app)
        if selected_provider not in active_execution_providers:
            raise RuntimeError(
                f"Selected {selected_provider} but active providers are "
                f"{', '.join(active_execution_providers)}"
            )
    except Exception as exc:
        if requested_provider != "auto" or selected_provider != "CUDAExecutionProvider":
            raise RuntimeError(
                f"FACE_EXECUTION_PROVIDER={requested_provider} could not initialize "
                f"{selected_provider}: {exc}"
            ) from exc
        logger.warning(
            "insightface_cuda_initialization_failed_retrying_cpu | reason=%s",
            exc,
        )
        selected_provider = "CPUExecutionProvider"
        app = _create_face_app(selected_provider)
        active_execution_providers = _app_execution_providers(app)

    face_app = app

    logger.info(
        "insightface_initialization_completed | model=%s | requestedProvider=%s | selectedProvider=%s | activeProviders=%s | durationMs=%.2f",
        settings.INSIGHTFACE_MODEL_NAME,
        requested_provider,
        selected_provider,
        active_execution_providers,
        (time.perf_counter() - started_at) * 1000,
    )


def is_face_app_initialized() -> bool:
    return face_app is not None


def get_active_execution_providers() -> tuple[str, ...]:
    return active_execution_providers


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
