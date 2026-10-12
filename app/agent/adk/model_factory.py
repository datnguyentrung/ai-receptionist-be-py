"""ADK Model Factory for shared Gemini configuration."""

import os

from google.adk.models.google_llm import Gemini
from google.genai.types import HttpRetryOptions

from app.core.config import settings


def create_model() -> Gemini:
    """Khởi tạo model Gemini với cấu hình chung."""
    return Gemini(
        model=os.getenv(
            "GOOGLE_ADK_MODEL",
            "gemini-3.5-flash-lite",
        ),
        client_kwargs={
            "api_key": settings.GEMINI_API_KEY,
        },
        retry_options=HttpRetryOptions(
            attempts=5,
            initial_delay=3.0,
            max_delay=30.0,
            http_status_codes=[429, 503],
        ),
    )


__all__ = ["create_model"]
