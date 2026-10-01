"""Lifecycle-owned dependencies for the standalone ADK ingestion server."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
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
from app.services.ingestion.graph_store import Neo4jIngestionStore
from app.services.ingestion.ontology import OntologyCache
from app.services.ingestion.orchestrator import IngestionOrchestrator
from app.services.ingestion.repository import IngestionRepository

logger = logging.getLogger(__name__)
SKILL_DIR = Path(__file__).resolve().parents[1] / "agent" / "skills" / "ingestion"


@dataclass
class ServiceContainer:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    neo4j_driver: AsyncDriver
    ontology_cache: OntologyCache
    orchestrator: IngestionOrchestrator
    ready: bool = False

    async def shutdown(self) -> None:
        self.ready = False
        self.ontology_cache.clear()
        await self.neo4j_driver.close()
        await self.engine.dispose()


_container: ServiceContainer | None = None


def get_service_container() -> ServiceContainer:
    if _container is None or not _container.ready:
        raise RuntimeError("ADK ingestion runtime is not ready")
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
        await driver.verify_connectivity()
        ontology_cache = OntologyCache(session_factory)
        await ontology_cache.warm()
        repository = IngestionRepository(session_factory)
        graph_store = Neo4jIngestionStore(driver, settings.NEO4J_DATABASE)
        orchestrator = IngestionOrchestrator(
            repository,
            graph_store,
            ontology_cache,
            max_file_size=settings.INGESTION_MAX_FILE_SIZE_BYTES,
            chunk_size_chars=settings.INGESTION_CHUNK_SIZE_CHARS,
            batch_size=settings.INGESTION_BATCH_SIZE,
            skill_digest=skill_content_digest(SKILL_DIR),
            model_id=settings.GOOGLE_ADK_MODEL,
        )
        return ServiceContainer(
            engine=engine,
            session_factory=session_factory,
            neo4j_driver=driver,
            ontology_cache=ontology_cache,
            orchestrator=orchestrator,
            ready=True,
        )
    except Exception:
        await driver.close()
        await engine.dispose()
        raise


@asynccontextmanager
async def ingestion_lifespan(app: FastAPI):
    global _container
    container = await build_service_container()
    _container = container
    app.state.services = container
    app.state.ready = True
    logger.info("ADK ingestion runtime is ready")
    try:
        yield
    finally:
        app.state.ready = False
        try:
            await asyncio.wait_for(
                container.shutdown(),
                timeout=settings.INGESTION_SHUTDOWN_TIMEOUT_SECONDS,
            )
        finally:
            _container = None
        logger.info("ADK ingestion runtime stopped")


def _validate_settings() -> None:
    required = {
        "database URL": settings.async_database_url,
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


__all__ = [
    "ServiceContainer",
    "build_service_container",
    "get_service_container",
    "ingestion_lifespan",
]
