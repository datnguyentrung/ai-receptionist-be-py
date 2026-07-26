import faulthandler
import logging
import sys
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.core.config import settings
from app.exceptions.exception_handler import (
    face_embedding_exception_handler,
    face_embedding_validation_exception_handler,
    internal_exception_handler,
)
from app.exceptions.face_embedding_exception import FaceEmbeddingException
from app.utils.insightface_utils import initialize_face_app, is_face_app_initialized

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if not faulthandler.is_enabled():
    faulthandler.enable(file=sys.stdout, all_threads=True)


def configure_logging() -> None:
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    else:
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.disabled = False
        uvicorn_logger.setLevel(logging.INFO)


configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        initialize_face_app()
    except Exception:
        logger.exception("InsightFace model initialization failed")

    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan,
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    request.state.request_id = request_id

    method = request.method
    path = request.url.path
    client = request.client.host if request.client else "unknown"
    start_time = time.perf_counter()

    logger.info(
        "Request started | requestId=%s | method=%s | path=%s | client=%s",
        request_id,
        method,
        path,
        client,
    )

    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.exception(
            "Request failed | requestId=%s | method=%s | path=%s | "
            "durationMs=%.2f | client=%s",
            request_id,
            method,
            path,
            duration_ms,
            client,
        )
        raise

    duration_ms = (time.perf_counter() - start_time) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "Request completed | requestId=%s | method=%s | path=%s | "
        "status=%s | durationMs=%.2f | client=%s",
        request_id,
        method,
        path,
        response.status_code,
        duration_ms,
        client,
    )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.add_exception_handler(FaceEmbeddingException, face_embedding_exception_handler)
app.add_exception_handler(
    RequestValidationError,
    face_embedding_validation_exception_handler,
)
app.add_exception_handler(Exception, internal_exception_handler)


@app.get("/", tags=["Root"])
async def root_check():
    return {"message": "Face embedding service is running"}


@app.get("/health", tags=["Health"])
async def health_check():
    model_ready = is_face_app_initialized()
    return {
        "status": "UP" if model_ready else "DEGRADED",
        "modelReady": model_ready,
        "model": settings.INSIGHTFACE_MODEL_NAME,
        "version": settings.PROJECT_NAME,
    }
