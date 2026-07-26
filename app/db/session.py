from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.core.config import settings

# Thêm statement_cache_size=0 để tương thích với Supabase Pooler (PgBouncer/Supavisor)
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={
        "statement_cache_size": 0,  # Tương đương prepareThreshold=0 của Java
        "prepared_statement_cache_size": 0,
    },
)

# Session factory (dùng async_sessionmaker theo chuẩn SQLAlchemy 2.x của bạn)
AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)

Base = declarative_base()


# Dependency để dùng trong FastAPI
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
