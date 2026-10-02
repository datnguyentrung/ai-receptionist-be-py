"""Lifecycle-owned dependencies for the standalone ADK ingestion server."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from google.adk.plugins.base_plugin import BasePlugin
from neo4j import AsyncDriver, AsyncGraphDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.agent.skills.skill_loader import skill_content_digest
from app.core.config import settings
from app.services.graphrag.embeddings import GeminiEmbeddingProvider
from app.services.ingestion.graph_store import Neo4jIngestionStore
from app.services.ingestion.ontology import OntologyCache
from app.services.ingestion.repository import IngestionRepository

logger = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parents[1] / "agent" / "skills" / "ingestion"


@dataclass
class ServiceContainer:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    neo4j_driver: AsyncDriver
    ontology_cache: OntologyCache
    repository: IngestionRepository
    graph_store: Neo4jIngestionStore
    embedding_provider: GeminiEmbeddingProvider
    skill_digest: str
    model_id: str
    max_file_size: int
    chunk_size_chars: int
    batch_size: int
    ready: bool = False
    closed: bool = False

    async def shutdown(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.ready = False
        self.ontology_cache.clear()
        try:
            await self.neo4j_driver.close()
        finally:
            await self.engine.dispose()


_container: ServiceContainer | None = None
_container_lock = asyncio.Lock()


class RuntimeInitializationError(RuntimeError):
    """A sanitized runtime startup error associated with one dependency."""

    def __init__(self, stage: str, cause: Exception) -> None:
        self.stage = stage
        self.cause_type = type(cause).__name__
        super().__init__(f"{stage} initialization failed ({self.cause_type}): {cause}")


class IngestionRuntimePlugin(BasePlugin):
    """Close lazy-created dependencies when an ADK runner shuts down."""

    def __init__(self) -> None:
        super().__init__(name="ingestion_runtime")

    async def close(self) -> None:
        await shutdown_service_container()


async def get_service_container() -> ServiceContainer:
    """Lấy ServiceContainer của tiến trình Ingestion (tự động khởi tạo nếu chưa sẵn sàng)."""
    global _container
    if _container is not None and _container.ready:
        return _container
    async with _container_lock:
        if _container is None or not _container.ready:
            logger.info("Auto-initializing ADK ingestion runtime on-demand...")
            _container = await build_service_container()
        return _container


async def build_service_container() -> ServiceContainer:
    _validate_settings()
    engine = create_async_engine(
        settings.async_database_url,
        echo=settings.DB_ECHO,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        await driver.close()
        await engine.dispose()
        raise RuntimeInitializationError("postgres", exc) from exc
    try:
        await driver.verify_connectivity()
    except Exception as exc:
        await driver.close()
        await engine.dispose()
        raise RuntimeInitializationError("neo4j", exc) from exc
    try:
        embedding_provider = GeminiEmbeddingProvider(
            api_key=settings.GEMINI_API_KEY,
            model=settings.RAG_EMBEDDING_MODEL,
            dimension=settings.RAG_EMBEDDING_DIMENSION,
        )
    except Exception as exc:
        await driver.close()
        await engine.dispose()
        raise RuntimeInitializationError("embedding", exc) from exc
    ontology_cache = OntologyCache(session_factory)
    try:
        await ontology_cache.warm()
    except Exception as exc:
        await driver.close()
        await engine.dispose()
        raise RuntimeInitializationError("ontology", exc) from exc
    repository = IngestionRepository(session_factory)
    graph_store = Neo4jIngestionStore(
        driver,
        settings.NEO4J_DATABASE,
        embedding_provider=embedding_provider,
    )
    try:
        await graph_store.ensure_indexes(settings.RAG_EMBEDDING_DIMENSION)
    except Exception as exc:
        await driver.close()
        await engine.dispose()
        raise RuntimeInitializationError("neo4j-index", exc) from exc
    return ServiceContainer(
        engine=engine,
        session_factory=session_factory,
        neo4j_driver=driver,
        ontology_cache=ontology_cache,
        repository=repository,
        graph_store=graph_store,
        embedding_provider=embedding_provider,
        skill_digest=skill_content_digest(SKILL_DIR),
        model_id=settings.GOOGLE_ADK_MODEL,
        max_file_size=settings.INGESTION_MAX_FILE_SIZE_BYTES,
        chunk_size_chars=settings.INGESTION_CHUNK_SIZE_CHARS,
        batch_size=settings.INGESTION_BATCH_SIZE,
        ready=True,
    )


async def shutdown_service_container() -> None:
    """Close the process singleton once, regardless of its initialization path."""

    global _container
    async with _container_lock:
        container, _container = _container, None
    if container is None:
        return
    await asyncio.wait_for(
        container.shutdown(), timeout=settings.INGESTION_SHUTDOWN_TIMEOUT_SECONDS
    )


@asynccontextmanager
async def ingestion_lifespan(app: FastAPI):
    container = await get_service_container()
    app.state.services = container
    app.state.ready = True
    logger.info("ADK ingestion runtime is ready")
    try:
        yield
    finally:
        app.state.ready = False
        await shutdown_service_container()
        logger.info("ADK ingestion runtime stopped")


def _validate_settings() -> None:
    required = {
        "DB_HOST": settings.DB_HOST,
        "DB_USER": settings.DB_USER,
        "DB_PASSWORD": settings.DB_PASSWORD,
        "DB_NAME": settings.DB_NAME,
        "NEO4J_URI": settings.NEO4J_URI,
        "NEO4J_USERNAME": settings.NEO4J_USERNAME,
        "NEO4J_PASSWORD": settings.NEO4J_PASSWORD,
        "NEO4J_DATABASE": settings.NEO4J_DATABASE,
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise RuntimeError(f"Missing ADK ingestion configuration: {', '.join(missing)}")
    if settings.INGESTION_MAX_FILE_SIZE_BYTES <= 0:
        raise RuntimeError("INGESTION_MAX_FILE_SIZE_BYTES must be positive")
    if settings.INGESTION_BATCH_SIZE <= 0:
        raise RuntimeError("INGESTION_BATCH_SIZE must be positive")
    if settings.RAG_EMBEDDING_DIMENSION <= 0:
        raise RuntimeError("RAG_EMBEDDING_DIMENSION must be positive")


__all__ = [
    "IngestionRuntimePlugin",
    "RuntimeInitializationError",
    "ServiceContainer",
    "build_service_container",
    "get_service_container",
    "ingestion_lifespan",
    "shutdown_service_container",
]
