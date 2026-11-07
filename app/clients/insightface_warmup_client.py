from __future__ import annotations

import asyncio

import httpx


class InsightFaceWarmupClient:
    """Loopback HTTP adapter used by the MQTT relay before opening a kiosk."""

    def __init__(
        self,
        base_url: str,
        internal_secret: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=2.0,
            headers={"X-Fog-Internal-Secret": internal_secret},
        )

    async def check(self) -> bool:
        # A sensor can publish immediately after the relay connects. Retry the
        # loopback call while Uvicorn transitions from lifespan to serving.
        for attempt in range(3):
            try:
                response = await self._client.post("/internal/insightface/warmup")
                return response.status_code == 200 and bool(
                    response.json().get("ready")
                )
            except (httpx.HTTPError, ValueError):
                if attempt == 2:
                    return False
                await asyncio.sleep(0.1 * (attempt + 1))
        return False

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
