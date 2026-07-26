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

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

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
