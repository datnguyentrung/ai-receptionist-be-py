import asyncio
import contextlib
import faulthandler
import io
import logging
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import api_router
from app.clients.insightface_warmup_client import InsightFaceWarmupClient
from app.clients.java_attendance_client import JavaAttendanceClient
from app.core.config import settings
from app.db.base import Base
from app.db.session import engine
from app.exceptions.exception_handler import (
    face_embedding_exception_handler,
    face_embedding_validation_exception_handler,
    internal_exception_handler,
)
from app.exceptions.face_embedding_exception import FaceEmbeddingException
from app.schemas.mqtt_relay import MqttRelay
from app.services.broker import create_broker, disconnect_device_sessions
from app.services.device_registry import DeviceRegistry
from app.services.mqtt_registry_plugins import set_registry, set_relay_credential
from app.utils.insightface_utils import initialize_face_app, is_face_app_initialized

if isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if isinstance(sys.stderr, io.TextIOWrapper):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if not faulthandler.is_enabled():
    faulthandler.enable(file=sys.stdout, all_threads=True)


class _AbandonedWebSocketHandshakeFilter(logging.Filter):
    """Hide probes that open TCP then disconnect before a WS request exists."""

    def filter(self, record: logging.LogRecord) -> bool:
        exception = record.exc_info[1] if record.exc_info else None
        return not (
            record.getMessage() == "opening handshake failed"
            and exception is not None
            and type(exception).__name__ == "InvalidMessage"
            and "did not receive a valid HTTP request" in str(exception)
        )


_abandoned_websocket_handshake_filter = _AbandonedWebSocketHandshakeFilter()


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

    websocket_logger = logging.getLogger("websockets.server")
    if _abandoned_websocket_handshake_filter not in websocket_logger.filters:
        websocket_logger.addFilter(_abandoned_websocket_handshake_filter)


configure_logging()
logger = logging.getLogger(__name__)
DATABASE_INITIALIZATION_ATTEMPTS = 3


async def initialize_database() -> None:
    """Initialize the PostgreSQL schema, retrying transient pooler disconnects."""

    for attempt in range(1, DATABASE_INITIALIZATION_ATTEMPTS + 1):
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE SCHEMA IF NOT EXISTS ontology;"))
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database schema 'ontology' and tables initialized successfully")
            return
        except Exception as exc:
            await engine.dispose()
            if attempt == DATABASE_INITIALIZATION_ATTEMPTS:
                logger.exception(
                    "Database initialization failed after %s attempt(s)", attempt
                )
                return

            delay_seconds = float(attempt)
            logger.warning(
                "Database initialization attempt %s/%s failed (%s); retrying in %.0fs",
                attempt,
                DATABASE_INITIALIZATION_ATTEMPTS,
                type(exc).__name__,
                delay_seconds,
            )
            await asyncio.sleep(delay_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        initialize_face_app()
    except Exception:
        logger.exception("InsightFace model initialization failed")

    await initialize_database()

    configure_logging()
    if settings.load_fog_gateway_credential():
        logger.info("[Registry] Loaded local Fog Gateway credential")
    else:
        logger.warning("[Registry] No valid local Fog Gateway credential; registry sync is disabled")
    warmup_client = InsightFaceWarmupClient(
        settings.FOG_INTERNAL_BASE_URL,
        settings.FOG_INTERNAL_SECRET,
    )
    registry = DeviceRegistry(settings)
    await registry.load()
    set_registry(registry)
    set_relay_credential(
        settings.FOG_RELAY_USERNAME,
        settings.ensure_relay_credential(),
        (settings.ESP32_INPUT_TOPIC, settings.DEVICE_HEARTBEAT_TOPIC),
    )
    broker = create_broker(settings)
    relay = MqttRelay(settings, readiness_probe=warmup_client.check, registry=registry)
    java_client = JavaAttendanceClient(
        settings.JAVA_CLOUD_BASE_URL,
        settings.JAVA_FOG_SECRET,
        settings.JAVA_REQUEST_TIMEOUT_SECONDS,
    )
    app.state.java_attendance_client = java_client
    app.state.device_registry = registry
    broker_started = False
    relay_started = False

    try:
        await broker.start()
        broker_started = True

        logger.info(
            "[Broker] MQTT TCP listening on %s:%s",
            settings.MQTT_TCP_HOST,
            settings.MQTT_TCP_PORT,
        )
        logger.info(
            "[Broker] MQTT WebSocket listening on %s:%s",
            settings.MQTT_WS_HOST,
            settings.MQTT_WS_PORT,
        )
        await relay.start()
        relay_started = True
    except Exception:
        # HTTP remains available for health checks and image processing even if
        # the local broker cannot bind or the relay cannot connect.
        logger.exception("MQTT runtime failed to start")

    try:
        async def sync_registry() -> None:
            while True:
                try:
                    synced = await registry.sync()
                    await disconnect_device_sessions(broker, registry.take_invalidated_device_codes())
                    if not synced:
                        logger.warning("[Registry] Sync was not completed; retaining the local snapshot")
                except Exception:
                    # A malformed cloud response must not permanently stop the
                    # heartbeat loop or take down local PIR routing.
                    logger.exception("[Registry] Sync failed; retrying on the next interval")
                await asyncio.sleep(settings.FOG_SYNC_INTERVAL_SECONDS)

        sync_task = asyncio.create_task(sync_registry(), name="device-registry-sync")
        yield
    finally:
        sync_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sync_task
        if relay_started:
            with contextlib.suppress(Exception):
                await relay.stop()
        if broker_started:
            with contextlib.suppress(Exception):
                await broker.shutdown()
        await warmup_client.close()
        await java_client.close()
        await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan,
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next: Any):
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
app.add_exception_handler(
    FaceEmbeddingException, cast(Any, face_embedding_exception_handler)
)
app.add_exception_handler(
    RequestValidationError,
    cast(Any, face_embedding_validation_exception_handler),
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
