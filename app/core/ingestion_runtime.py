"""Quản lý tài nguyên kết nối runtime cho ADK Agent.

Mô-đun này cung cấp:
- `ServiceContainer`: Chứa toàn bộ kết nối DB (PostgreSQL, Neo4j), embedding và graph store.
- `AgentRuntimePlugin`: Plugin tích hợp vào ADK App để dọn dẹp tài nguyên khi runner tắt.
- Các hàm quản lý khởi tạo Singleton (`get_service_container`, `build_service_container`, `shutdown_service_container`).
- `runtime_lifespan`: Context manager dùng cho FastAPI Lifespan.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

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

from app.core.config import settings
from app.services.graphrag.embeddings import GeminiEmbeddingProvider
from app.services.graphrag.graph_store import Neo4jGraphStore
from app.services.ingestion.engine.graph_store import Neo4jIngestionStore
from app.services.ingestion.engine.repository import IngestionRepository
from app.services.ingestion.schema.ontology import OntologyCache
from app.services.ontology.ontology_compiler import OntologyCompiler
from app.services.ontology.ontology_lifecycle import OntologyLifecycle

logger = logging.getLogger(__name__)


@dataclass
class ServiceContainer:
    """Vỏ bọc (Container) chứa toàn bộ các dịch vụ và tài nguyên kết nối runtime."""

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    neo4j_driver: AsyncDriver
    graph_store: Neo4jIngestionStore
    rag_graph_store: Neo4jGraphStore
    embedding_provider: GeminiEmbeddingProvider
    repository: IngestionRepository
    ontology_cache: OntologyCache
    model_id: str
    ontology_lifecycle: OntologyLifecycle
    max_file_size: int = settings.INGESTION_MAX_FILE_SIZE_BYTES
    chunk_size_chars: int = settings.INGESTION_CHUNK_SIZE_CHARS
    batch_size: int = settings.INGESTION_BATCH_SIZE
    skill_digest: str = "v1"
    ready: bool = False
    closed: bool = False

    async def shutdown(self) -> None:
        """Đóng và giải phóng toàn bộ kết nối tài nguyên."""
        if self.closed:
            return
        self.closed = True
        self.ready = False
        try:
            await self.neo4j_driver.close()
        finally:
            await self.engine.dispose()


_container: ServiceContainer | None = None
_container_lock = asyncio.Lock()


class RuntimeInitializationError(RuntimeError):
    """Ngoại lệ chuẩn hóa khi gặp sự cố khởi tạo runtime."""

    def __init__(self, stage: str, cause: Exception) -> None:
        self.stage = stage
        self.cause_type = type(cause).__name__
        super().__init__(
            f"Khởi tạo runtime thất bại tại bước [{stage}] ({self.cause_type}): {cause}"
        )


class AgentRuntimePlugin(BasePlugin):
    """Plugin tích hợp vào ADK Agent App nhằm tự động giải phóng tài nguyên khi runner kết thúc."""

    def __init__(self) -> None:
        super().__init__(name="agent_runtime")

    async def close(self) -> None:
        await shutdown_service_container()


async def get_service_container() -> ServiceContainer:
    """Lấy ServiceContainer của tiến trình (Singleton)."""
    global _container
    if _container is not None and _container.ready:
        return _container
    async with _container_lock:
        if _container is None or not _container.ready:
            logger.info("Tự động khởi tạo ADK Runtime...")
            _container = await build_service_container()
        return _container


async def build_service_container() -> ServiceContainer:
    """Khởi tạo, cấu hình và kiểm tra kết nối toàn bộ các dịch vụ phụ thuộc."""
    _validate_settings()

    engine = create_async_engine(
        settings.async_database_url,
        echo=settings.DB_ECHO,
        pool_pre_ping=True,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
    )
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

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

    graph_store = Neo4jIngestionStore(
        driver,
        settings.NEO4J_DATABASE,
        embedding_provider=embedding_provider,
    )
    rag_graph_store = Neo4jGraphStore(
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

    repository = IngestionRepository()
    ontology_cache = OntologyCache(session_factory)
    ontology_compiler = OntologyCompiler()
    ontology_lifecycle = OntologyLifecycle(
        session_factory=session_factory,
        compiler=ontology_compiler,
        cache=ontology_cache,
    )

    return ServiceContainer(
        engine=engine,
        session_factory=session_factory,
        neo4j_driver=driver,
        graph_store=graph_store,
        rag_graph_store=rag_graph_store,
        embedding_provider=embedding_provider,
        repository=repository,
        ontology_cache=ontology_cache,
        model_id=settings.GOOGLE_ADK_MODEL,
        ontology_lifecycle=ontology_lifecycle,
        max_file_size=settings.INGESTION_MAX_FILE_SIZE_BYTES,
        chunk_size_chars=settings.INGESTION_CHUNK_SIZE_CHARS,
        batch_size=settings.INGESTION_BATCH_SIZE,
        skill_digest="v1",
        ready=True,
    )


async def shutdown_service_container() -> None:
    """Giải phóng và đóng ServiceContainer đơn lệ."""
    global _container
    async with _container_lock:
        container, _container = _container, None
    if container is None:
        return
    await asyncio.wait_for(
        container.shutdown(), timeout=settings.INGESTION_SHUTDOWN_TIMEOUT_SECONDS
    )


@asynccontextmanager
async def runtime_lifespan(app: FastAPI):
    """Lifespan context manager cho FastAPI."""
    container = await get_service_container()
    app.state.services = container
    app.state.ready = True
    logger.info("ADK Runtime đã sẵn sàng")
    try:
        yield
    finally:
        app.state.ready = False
        await shutdown_service_container()
        logger.info("ADK Runtime đã dừng")


def _validate_settings() -> None:
    """Xác thực cấu hình bắt buộc."""
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
        raise RuntimeError(f"Thiếu thông số cấu hình ADK: {', '.join(missing)}")
    if settings.RAG_EMBEDDING_DIMENSION <= 0:
        raise RuntimeError("RAG_EMBEDDING_DIMENSION phải lớn hơn 0")


__all__ = [
    "AgentRuntimePlugin",
    "RuntimeInitializationError",
    "ServiceContainer",
    "build_service_container",
    "get_service_container",
    "runtime_lifespan",
    "shutdown_service_container",
]
