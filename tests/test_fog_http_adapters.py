import json
from uuid import UUID

import httpx
import pytest

from app.clients.insightface_warmup_client import InsightFaceWarmupClient
from app.clients.java_attendance_client import JavaAttendanceClient


@pytest.mark.asyncio
async def test_java_adapter_sends_embedding_json_without_image() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(202)

    async with httpx.AsyncClient(
        base_url="http://java.test",
        transport=httpx.MockTransport(handler),
        headers={"X-Fog-Secret": "fog-secret"},
    ) as http_client:
        adapter = JavaAttendanceClient(
            "http://unused", "unused", 1, client=http_client
        )
        request_id = UUID("a121fd2d-c47b-4566-80c8-26b73777f941")
        await adapter.submit_embedding(request_id, "kiosk-01", [0.25] * 512)

    request = captured[0]
    assert request.url.path == (
        "/api/v1/internal/face-check-in-scans/"
        "a121fd2d-c47b-4566-80c8-26b73777f941/embedding"
    )
    assert request.headers["X-Fog-Secret"] == "fog-secret"
    body = json.loads(request.content)
    assert body.keys() == {"deviceId", "embedding"}
    assert len(body["embedding"]) == 512


@pytest.mark.asyncio
async def test_warmup_adapter_pings_loopback_endpoint() -> None:
    captured: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ready": True, "model": "buffalo_l"})

    async with httpx.AsyncClient(
        base_url="http://fog.test",
        transport=httpx.MockTransport(handler),
        headers={"X-Fog-Internal-Secret": "local-secret"},
    ) as http_client:
        adapter = InsightFaceWarmupClient(
            "http://unused", "unused", client=http_client
        )
        assert await adapter.check() is True

    assert captured[0].url.path == "/internal/insightface/warmup"
    assert captured[0].headers["X-Fog-Internal-Secret"] == "local-secret"
