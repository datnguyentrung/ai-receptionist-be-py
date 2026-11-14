from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.config import settings

router = APIRouter(prefix="/local/device-provisioning", tags=["Device provisioning"])


class Esp32ClaimRequest(BaseModel):
    code: str = Field(min_length=8, max_length=256)
    hardwareId: str = Field(min_length=4, max_length=128)
    firmwareVersion: str | None = Field(default=None, max_length=100)
    hardwareVersion: str | None = Field(default=None, max_length=100)


@router.post("/esp32/claim")
async def claim_esp32(payload: Esp32ClaimRequest, request: Request) -> dict[str, Any]:
    """LAN-only proxy; Java remains the authority for the one-time claim."""
    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1"} and not client_host.startswith(("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Local network required")
    body = {"code": payload.code, "deviceType": "ESP32", "hardwareId": payload.hardwareId,
            "firmwareVersion": payload.firmwareVersion, "hardwareVersion": payload.hardwareVersion}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{settings.JAVA_CLOUD_BASE_URL.rstrip('/')}/api/v1/device-provisioning/claim", json=body)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail="Provisioning claim rejected") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Java device service unavailable") from exc
