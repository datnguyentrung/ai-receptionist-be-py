from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    PROJECT_NAME: str = "AI Receptionist API"
    DATABASE_URL: str = "postgresql+asyncpg://root:31102005@localhost:5433/version_4"
    FACE_MATCH_THRESHOLD: float = 0.7
    GEMINI_API_KEY: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    SERVER_PUBLIC_URL: str = ""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")


# Khởi tạo một biến settings để import dùng ở mọi nơi
settings = Settings()
