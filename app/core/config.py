from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Receptionist Face Embedding API"
    INSIGHTFACE_MODEL_NAME: str = "buffalo_l"
    INSIGHTFACE_PROVIDERS: str = "CPUExecutionProvider"
    FACE_EMBEDDING_DIMENSION: int = 512
    MAX_IMAGE_SIZE_BYTES: int = 5 * 1024 * 1024
    ALLOWED_IMAGE_TYPES: str = "image/jpeg,image/png"
    GEMINI_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    SERVER_PUBLIC_URL: str = ""

    # Database Configuration (PostgreSQL / Supabase)
    DB_HOST: str = "aws-0-ap-southeast-1.pooler.supabase.com"
    DB_PORT: int = 5432
    DB_USER: str = "postgres.enjbieisclwmyrgyskha"
    DB_PASSWORD: str = ""
    DB_NAME: str = "postgres"
    DATABASE_URL: str = ""
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    @property
    def async_database_url(self) -> str:
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+asyncpg://", 1)
            return url
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}@"
            f"{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

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


settings = Settings()
