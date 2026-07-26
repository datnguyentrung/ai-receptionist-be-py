import faulthandler
import logging
import sys
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import api_router
from app.core.config import settings
from app.db.session import engine
from app.exceptions.app_exception import AppException
from app.utils.insightface_utils import ensure_face_app_initialized

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

    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.disabled = False
        uvicorn_logger.setLevel(logging.INFO)


configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        logger.info("Checking database connection")
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection successful")
    except Exception:
        logger.exception("Database connection failed")

    try:
        logger.info("Initializing InsightFace model")
        ensure_face_app_initialized()
        logger.info("InsightFace model initialized successfully")
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
        "Request started | request_id=%s | method=%s | path=%s | client=%s",
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
            "Request failed | request_id=%s | method=%s | path=%s | "
            "duration_ms=%.2f | client=%s",
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
        "Request completed | request_id=%s | method=%s | path=%s | "
        "status=%s | duration_ms=%.2f | client=%s",
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


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    error_content = {
        "status_code": exc.error_code.status_code,
        "message": exc.error_code.message,
    }

    if exc.detail_message:
        error_content["detail"] = exc.detail_message

    return JSONResponse(
        status_code=exc.error_code.status_code,
        content=error_content,
    )


@app.get("/", tags=["Root"])
async def root_check():
    return {"message": "Hugging Face Space is running smoothly!"}


@app.get("/health", tags=["Health"])
async def health_check():
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        return {
            "status": "UP",
            "database": "CONNECTED",
            "version": settings.PROJECT_NAME,
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "DOWN", "database": "ERROR", "detail": str(e)},
        )
