from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.commands.provision_fog import write_credential

router = APIRouter(prefix="/local/device-provisioning", tags=["Device provisioning"])
logger = logging.getLogger(__name__)
PAIRING_TTL_SECONDS = 90
_pairing_sessions: dict[str, dict[str, Any]] = {}


class Esp32ClaimRequest(BaseModel):
    code: str = Field(min_length=8, max_length=256)
    hardwareId: str = Field(min_length=4, max_length=128)
    firmwareVersion: str | None = Field(default=None, max_length=100)
    hardwareVersion: str | None = Field(default=None, max_length=100)


class FogClaimRequest(BaseModel):
    code: str = Field(min_length=8, max_length=256)


class Esp32PairingStartRequest(BaseModel):
    hardwareId: str = Field(min_length=4, max_length=128)
    firmwareVersion: str | None = Field(default=None, max_length=100)
    hardwareVersion: str | None = Field(default=None, max_length=100)


class Esp32PairingApprovalRequest(BaseModel):
    code: str = Field(min_length=8, max_length=256)


def _lan_client(request: Request) -> str:
    client_host = request.client.host if request.client else ""
    if client_host in {"127.0.0.1", "::1"} or client_host.startswith(("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")):
        return client_host
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Local network required")


def _active_pairing_sessions() -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    for session_id, session in list(_pairing_sessions.items()):
        if session["expiresAt"] <= now:
            logger.info("[Pairing] Expired | sessionId=%s | hardwareId=%s", session_id, session["hardwareId"])
            del _pairing_sessions[session_id]
    return list(_pairing_sessions.values())


@router.post("/esp32/pairing-sessions")
async def start_esp32_pairing(payload: Esp32PairingStartRequest, request: Request) -> dict[str, Any]:
    """Open a short physical-presence pairing window after the ESP BOOT hold."""
    client_host = _lan_client(request)
    sessions = _active_pairing_sessions()
    existing = next((session for session in sessions if session["hardwareId"] == payload.hardwareId and session["clientHost"] == client_host), None)
    if existing:
        return {"sessionId": existing["sessionId"], "status": existing["status"], "expiresAt": existing["expiresAt"].isoformat()}
    session_id = str(uuid4())
    session = {
        "sessionId": session_id,
        "hardwareId": payload.hardwareId,
        "clientHost": client_host,
        "firmwareVersion": payload.firmwareVersion,
        "hardwareVersion": payload.hardwareVersion,
        "status": "WAITING",
        "code": None,
        "expiresAt": datetime.now(UTC) + timedelta(seconds=PAIRING_TTL_SECONDS),
    }
    _pairing_sessions[session_id] = session
    logger.info("[Pairing] Started | sessionId=%s | hardwareId=%s", session_id, payload.hardwareId)
    return {"sessionId": session_id, "status": "WAITING", "expiresAt": session["expiresAt"].isoformat()}


@router.get("/esp32/pairing-sessions")
async def list_esp32_pairing_sessions(request: Request) -> list[dict[str, Any]]:
    _lan_client(request)
    return [{
        "sessionId": session["sessionId"],
        "hardwareId": session["hardwareId"],
        "firmwareVersion": session["firmwareVersion"],
        "hardwareVersion": session["hardwareVersion"],
        "status": session["status"],
        "expiresAt": session["expiresAt"].isoformat(),
    } for session in _active_pairing_sessions()]


@router.post("/esp32/pairing-sessions/{session_id}/approve")
async def approve_esp32_pairing(session_id: str, payload: Esp32PairingApprovalRequest, request: Request) -> dict[str, str]:
    _lan_client(request)
    _active_pairing_sessions()
    session = _pairing_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pairing session expired or not found")
    if session["status"] != "WAITING":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Pairing session is no longer waiting")
    session["code"] = payload.code
    session["status"] = "APPROVED"
    logger.info("[Pairing] Approved | sessionId=%s | hardwareId=%s", session_id, session["hardwareId"])
    return {"status": "APPROVED"}


@router.get("/esp32/pairing-sessions/{session_id}")
async def poll_esp32_pairing(session_id: str, request: Request) -> dict[str, Any]:
    client_host = _lan_client(request)
    _active_pairing_sessions()
    session = _pairing_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pairing session expired or not found")
    if session["clientHost"] != client_host:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Pairing session belongs to another device")
    response: dict[str, Any] = {"status": session["status"]}
    if session["status"] == "APPROVED":
        response["code"] = session["code"]
    return response


@router.post("/esp32/pairing-sessions/{session_id}/complete")
async def complete_esp32_pairing(session_id: str, request: Request) -> dict[str, str]:
    client_host = _lan_client(request)
    session = _pairing_sessions.get(session_id)
    if session is None or session["clientHost"] != client_host:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pairing session expired or not found")
    logger.info("[Pairing] Claimed | sessionId=%s | hardwareId=%s", session_id, session["hardwareId"])
    del _pairing_sessions[session_id]
    return {"status": "CLAIMED"}


@router.post("/esp32/claim")
async def claim_esp32(payload: Esp32ClaimRequest, request: Request) -> dict[str, Any]:
    """LAN-only proxy; Java remains the authority for the one-time claim."""
    _lan_client(request)
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


@router.post("/fog/claim")
async def claim_fog(payload: FogClaimRequest, request: Request) -> dict[str, str]:
    """Enroll this Fog from a LAN admin app and keep the credential on this host."""
    _lan_client(request)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.JAVA_CLOUD_BASE_URL.rstrip('/')}/api/v1/device-provisioning/claim",
                json={"code": payload.code, "deviceType": "FOG_GATEWAY", "installationId": "fog-host"},
            )
            response.raise_for_status()
            claim = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail="Fog provisioning claim rejected") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Java device service unavailable") from exc
    if not claim.get("deviceCode") or not claim.get("secret"):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Java provisioning response is incomplete")
    write_credential({"javaUrl": settings.JAVA_CLOUD_BASE_URL.rstrip("/"), "deviceCode": claim["deviceCode"], "secret": claim["secret"]})
    settings.load_fog_gateway_credential()
    return {"deviceCode": claim["deviceCode"], "status": "PROVISIONED"}
