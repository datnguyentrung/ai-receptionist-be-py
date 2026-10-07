from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Receptionist Face Embedding API"
    INSIGHTFACE_MODEL_NAME: str = "buffalo_l"
    INSIGHTFACE_PROVIDERS: str = "CPUExecutionProvider"
    FACE_EMBEDDING_DIMENSION: int = 512
    MAX_IMAGE_SIZE_BYTES: int = 5 * 1024 * 1024
    ALLOWED_IMAGE_TYPES: str = "image/jpeg,image/png"
    GEMINI_API_KEY: str = Field(
        default="",
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    SERVER_PUBLIC_URL: str = ""

    # Standalone ADK Web / ingestion runtime
    GOOGLE_ADK_MODEL: str = "gemini-3.5-flash-lite"
    ADK_WEB_HOST: str = "127.0.0.1"
    ADK_WEB_PORT: int = 8001
    ADK_ALLOWED_ORIGINS: str = "http://localhost:8001,http://127.0.0.1:8001"
    ADK_SESSION_DB_URI: str = "sqlite:///./.adk/sessions.db"
    INGESTION_MAX_FILE_SIZE_BYTES: int = 20 * 1024 * 1024
    INGESTION_CHUNK_SIZE_CHARS: int = 6000
    INGESTION_BATCH_SIZE: int = 5
    INGESTION_SHUTDOWN_TIMEOUT_SECONDS: float = 15.0

    # GraphRAG retrieval/indexing
    RAG_EMBEDDING_MODEL: str = "gemini-embedding-001"
    RAG_EMBEDDING_DIMENSION: int = 768
    RAG_MIN_COSINE_SCORE: float = 0.55
    RAG_DEFAULT_TOP_K: int = 10
    RAG_CANDIDATE_LIMIT: int = 20
    RAG_CONTEXT_MAX_CHARS: int = 24000

    NEO4J_URI: str = ""
    NEO4J_USERNAME: str = ""
    NEO4J_PASSWORD: str = ""
    NEO4J_DATABASE: str = "neo4j"

    # Database Configuration (PostgreSQL / Supabase)
    DB_HOST: str = "aws-0-ap-southeast-1.pooler.supabase.com"
    DB_PORT: int = 5432
    DB_USER: str = "postgres.enjbieisclwmyrgyskha"
    DB_PASSWORD: str = ""
    DB_NAME: str = "postgres"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10

    # Ingestion
    INGESTION_MAX_BATCH_CHUNKS: int = 5
    INGESTION_MAX_BATCH_CHARS: int = 15000
    INGESTION_TRUE_CHUNK_CACHE: bool = True
    INGESTION_ESTIMATED_CHARS_PER_TOKEN: float = 2.0
    INGESTION_MAX_BATCH_ESTIMATED_TOKENS: int = 15000

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    @property
    def async_database_url(self) -> str:
        """Build the PostgreSQL DSN exclusively from the DB_* settings.

        URL.create handles reserved characters in credentials without leaking
        precedence to an unrelated DATABASE_URL environment variable.
        """

        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        ).render_as_string(hide_password=False)

    @property
    def insightface_providers(self) -> list[str]:
        return [
            provider.strip()
            for provider in self.INSIGHTFACE_PROVIDERS.split(",")
            if provider.strip()
        ]

    @property
    def allowed_image_types(self) -> set[str]:
        return {
            content_type.strip().lower()
            for content_type in self.ALLOWED_IMAGE_TYPES.split(",")
            if content_type.strip()
        }

    @property
    def adk_allowed_origins(self) -> list[str]:
        return [
            value.strip()
            for value in self.ADK_ALLOWED_ORIGINS.split(",")
            if value.strip()
        ]


settings = Settings()
