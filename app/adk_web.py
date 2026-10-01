"""Standalone ADK Web UI and API for Taekwondo ingestion.

Run with:
    python -m app.adk_web
"""

from __future__ import annotations

from pathlib import Path

import uvicorn
from google.adk.cli.fast_api import get_fast_api_app

from app.core.config import settings
from app.core.ingestion_runtime import ingestion_lifespan

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = PROJECT_ROOT / "adk_agents"

app = get_fast_api_app(
    agents_dir=str(AGENTS_DIR),
    session_service_uri=settings.ADK_SESSION_DB_URI,
    allow_origins=settings.adk_allowed_origins,
    web=True,
    a2a=False,
    host=settings.ADK_WEB_HOST,
    port=settings.ADK_WEB_PORT,
    lifespan=ingestion_lifespan,
    use_local_storage=True,
    auto_create_session=True,
)


@app.get("/health/live", tags=["Health"])
async def liveness() -> dict[str, str]:
    return {"status": "UP"}


@app.get("/health/ready", tags=["Health"])
async def readiness() -> dict[str, object]:
    container = getattr(app.state, "services", None)
    ready = bool(container and container.ready and getattr(app.state, "ready", False))
    return {"status": "UP" if ready else "DOWN", "ready": ready}


def main() -> None:
    uvicorn.run(
        "app.adk_web:app",
        host=settings.ADK_WEB_HOST,
        port=settings.ADK_WEB_PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
