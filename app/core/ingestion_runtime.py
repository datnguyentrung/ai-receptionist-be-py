"""Quản lý vòng đời (Lifecycle) và tài nguyên kết nối runtime cho tiến trình ADK Ingestion.

Mô-đun này cung cấp:
- `ServiceContainer`: Vỏ bọc chứa toàn bộ kết nối DB (PostgreSQL, Neo4j), cache ontology, embedding và repositories.
- `IngestionRuntimePlugin`: Plugin tích hợp vào ADK App để dọn dẹp tài nguyên khi runner tắt.
- Các hàm quản lý khởi tạo Singleton (`get_service_container`, `build_service_container`, `shutdown_service_container`).
- `ingestion_lifespan`: Context manager dùng cho FastAPI Lifespan.
"""

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
from app.services.ontology_compiler import OntologyCompiler
from app.services.ontology_lifecycle import OntologyLifecycle
from app.utils.ingestion_logger import reset_log_file

logger = logging.getLogger(__name__)
# Đường dẫn tới thư mục skill Ingestion để tính toán mã băm kiểm tra tính toàn vẹn (digest)
SKILL_DIR = Path(__file__).resolve().parents[1] / "agent" / "skills" / "ingestion"


@dataclass
class ServiceContainer:
    """Vỏ bọc (Container) chứa toàn bộ các dịch vụ và tài nguyên kết nối dùng cho tiến trình Ingestion.

    Bao gồm:
        - `engine`: SQLAlchemy AsyncEngine quản lý kết nối cơ sở dữ liệu PostgreSQL.
        - `session_factory`: Bộ tạo session bất đồng bộ (`AsyncSession`) phục vụ truy vấn DB.
        - `neo4j_driver`: Driver kết nối cơ sở dữ liệu đồ thị Neo4j.
        - `ontology_cache`: Bộ nhớ đệm lưu trữ sơ đồ Ontology nạp từ CSDL.
        - `repository`: Bộ nhớ tạm trong tiến trình cho workspace Ingestion.
        - `graph_store`: Lớp lưu trữ và cập nhật các Node/Relationship vào Neo4j.
        - `embedding_provider`: Dịch vụ tạo vector embeddings qua Gemini API.
        - `skill_digest`: Mã băm SHA-256 phản ánh nội dung của SKILL.md.
        - `model_id`: Tên mô hình LLM được sử dụng (VD: gemini-3.5-flash-lite).
        - `max_file_size`: Giới hạn dung lượng file tải lên (bytes).
        - `chunk_size_chars`: Số lượng ký tự tối đa cho mỗi chunk văn bản.
        - `batch_size`: Số chunk được gom vào mỗi batch xử lý.
        - `ready`: Cờ đánh dấu các dịch vụ đã khởi tạo hoàn tất và sẵn sàng.
        - `closed`: Cờ đánh dấu tài nguyên đã được giải phóng/đóng.
    """

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    neo4j_driver: AsyncDriver
    ontology_cache: OntologyCache
    repository: IngestionRepository
    graph_store: Neo4jIngestionStore
    embedding_provider: GeminiEmbeddingProvider
    ontology_compiler: OntologyCompiler
    ontology_lifecycle: OntologyLifecycle
    skill_digest: str
    model_id: str
    max_file_size: int
    chunk_size_chars: int
    batch_size: int
    ready: bool = False
    closed: bool = False

    async def shutdown(self) -> None:
        """Đóng và giải phóng toàn bộ kết nối tài nguyên (Neo4j driver, PostgreSQL engine, cache)."""
        if self.closed:
            return
        self.closed = True
        self.ready = False
        self.ontology_cache.clear()
        try:
            await self.neo4j_driver.close()
        finally:
            await self.engine.dispose()


# Biến Singleton toàn cục lưu giữ ServiceContainer và khóa bất đồng bộ để tránh race condition khi khởi tạo
_container: ServiceContainer | None = None
_container_lock = asyncio.Lock()


class RuntimeInitializationError(RuntimeError):
    """Ngoại lệ chuẩn hóa khi gặp sự cố khởi tạo một thành phần phụ thuộc của runtime."""

    def __init__(self, stage: str, cause: Exception) -> None:
        self.stage = stage
        self.cause_type = type(cause).__name__
        super().__init__(
            f"Khởi tạo runtime thất bại tại bước [{stage}] ({self.cause_type}): {cause}"
        )


class IngestionRuntimePlugin(BasePlugin):
    """Plugin tích hợp vào ADK Agent App nhằm tự động giải phóng tài nguyên khi runner kết thúc."""

    def __init__(self) -> None:
        super().__init__(name="ingestion_runtime")

    async def close(self) -> None:
        """Được ADK Runner gọi khi ứng dụng agent tắt."""
        await shutdown_service_container()


async def get_service_container() -> ServiceContainer:
    """Lấy ServiceContainer của tiến trình Ingestion (tự động khởi tạo theo cơ chế Singleton nếu chưa có).

    Trả về:
        Đối tượng `ServiceContainer` đã khởi tạo và sẵn sàng phục vụ các Ingestion Tools.
    """
    global _container
    if _container is not None and _container.ready:
        return _container
    async with _container_lock:
        if _container is None or not _container.ready:
            logger.info("Tự động khởi tạo ADK Ingestion Runtime...")
            _container = await build_service_container()
        return _container


async def build_service_container() -> ServiceContainer:
    """Khởi tạo, cấu hình và kiểm tra kết nối (health check) toàn bộ các dịch vụ phụ thuộc.

    Các bước thực hiện:
        1. Xóa file log cũ của Ingestion để bắt đầu phiên mới.
        2. Kiểm tra các biến môi trường cấu hình bắt buộc.
        3. Khởi tạo SQLAlchemy Engine & kiểm tra kết nối PostgreSQL.
        4. Khởi tạo Neo4j Driver & kiểm tra kết nối Neo4j.
        5. Khởi tạo dịch vụ Gemini Embedding.
        6. Nạp phiên bản Ontology hiện hành và catalog scope nhẹ.
        7. Đảm bảo các chỉ mục (indexes, vector index) trên Neo4j đã được tạo sẵn.
        8. Đóng gói và trả về đối tượng `ServiceContainer`.

    Trả về:
        `ServiceContainer` chứa đầy đủ các đối tượng đã được kiểm tra sẵn sàng.

    Ném ra:
        `RuntimeInitializationError`: Nếu có bất kỳ bước kết nối nào gặp sự cố.
    """
    reset_log_file()
    _validate_settings()

    # Khởi tạo kết nối CSDL quan hệ PostgreSQL
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

    # Khởi tạo kết nối CSDL đồ thị Neo4j
    driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USERNAME, settings.NEO4J_PASSWORD),
    )

    # Kiểm tra kết nối PostgreSQL
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        await driver.close()
        await engine.dispose()
        raise RuntimeInitializationError("postgres", exc) from exc

    # Kiểm tra kết nối Neo4j
    try:
        await driver.verify_connectivity()
    except Exception as exc:
        await driver.close()
        await engine.dispose()
        raise RuntimeInitializationError("neo4j", exc) from exc

    # Khởi tạo Gemini Embedding Provider
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

    # Chỉ nạp active version và catalog; snapshot chi tiết được tải theo từng batch.
    ontology_cache = OntologyCache(session_factory)
    try:
        await ontology_cache.warm()
    except Exception as exc:
        await driver.close()
        await engine.dispose()
        raise RuntimeInitializationError("ontology", exc) from exc

    repository = IngestionRepository()
    ontology_compiler = OntologyCompiler()
    ontology_lifecycle = OntologyLifecycle(
        session_factory, ontology_compiler, ontology_cache
    )
    graph_store = Neo4jIngestionStore(
        driver,
        settings.NEO4J_DATABASE,
        embedding_provider=embedding_provider,
    )

    # Đảm bảo các chỉ mục tìm kiếm và Vector index trên Neo4j đã sẵn sàng
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
        ontology_compiler=ontology_compiler,
        ontology_lifecycle=ontology_lifecycle,
        skill_digest=skill_content_digest(SKILL_DIR),
        model_id=settings.GOOGLE_ADK_MODEL,
        max_file_size=settings.INGESTION_MAX_FILE_SIZE_BYTES,
        chunk_size_chars=settings.INGESTION_CHUNK_SIZE_CHARS,
        batch_size=settings.INGESTION_BATCH_SIZE,
        ready=True,
    )


async def shutdown_service_container() -> None:
    """Giải phóng và đóng ServiceContainer đơn lệ (Singleton) một cách an toàn, có timeout."""
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
    """Context manager phục vụ quản lý vòng đời ứng dụng FastAPI (Lifespan).

    - Khi khởi động server: Khởi tạo ServiceContainer và gắn vào `app.state`.
    - Khi tắt server: Dọn dẹp và giải phóng toàn bộ kết nối DB/Neo4j.
    """
    container = await get_service_container()
    app.state.services = container
    app.state.ready = True
    logger.info("ADK Ingestion Runtime đã sẵn sàng")
    try:
        yield
    finally:
        app.state.ready = False
        await shutdown_service_container()
        logger.info("ADK Ingestion Runtime đã dừng")


def _validate_settings() -> None:
    """Xác thực tính hợp lệ của các thông số cấu hình và biến môi trường bắt buộc cho Ingestion."""
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
        raise RuntimeError(
            f"Thiếu thông số cấu hình ADK Ingestion: {', '.join(missing)}"
        )
    if settings.INGESTION_MAX_FILE_SIZE_BYTES <= 0:
        raise RuntimeError("INGESTION_MAX_FILE_SIZE_BYTES phải lớn hơn 0")
    if settings.INGESTION_BATCH_SIZE <= 0:
        raise RuntimeError("INGESTION_BATCH_SIZE phải lớn hơn 0")
    if settings.RAG_EMBEDDING_DIMENSION <= 0:
        raise RuntimeError("RAG_EMBEDDING_DIMENSION phải lớn hơn 0")


__all__ = [
    "IngestionRuntimePlugin",
    "RuntimeInitializationError",
    "ServiceContainer",
    "build_service_container",
    "get_service_container",
    "ingestion_lifespan",
    "shutdown_service_container",
]
