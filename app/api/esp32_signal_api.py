from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

router = APIRouter(prefix="/local/esp32", tags=["ESP32 Signal"])
logger = logging.getLogger(__name__)

SignalLevel = Literal["LOW", "HIGH"]
SignalTransition = Literal["LOW_TO_HIGH", "HIGH_TO_LOW", "UNCHANGED"]

_current_signal: SignalLevel = "LOW"
_subscribers: set[asyncio.Queue[dict[str, str]]] = set()


class Esp32PresenceRequest(BaseModel):
    signal: SignalLevel

    @field_validator("signal", mode="before")
    @classmethod
    def normalize_signal(cls, value: object) -> str:
        if isinstance(value, bool):
            return "HIGH" if value else "LOW"
        if isinstance(value, (int, float)):
            return "HIGH" if value != 0 else "LOW"
        if isinstance(value, str):
            normalized = value.strip().upper()
            if normalized in {"1", "TRUE", "HIGH"}:
                return "HIGH"
            if normalized in {"0", "FALSE", "LOW"}:
                return "LOW"
        raise ValueError("signal must be LOW or HIGH")


def _resolve_transition(previous: SignalLevel, current: SignalLevel) -> SignalTransition:
    if previous == "LOW" and current == "HIGH":
        return "LOW_TO_HIGH"
    if previous == "HIGH" and current == "LOW":
        return "HIGH_TO_LOW"
    return "UNCHANGED"


@router.get("/presence")
async def get_esp32_presence() -> dict[str, str]:
    return {"signal": _current_signal}


@router.post("/presence")
async def post_esp32_presence(payload: Esp32PresenceRequest) -> dict[str, str]:
    global _current_signal
    previous = _current_signal
    current = payload.signal
    transition = _resolve_transition(previous, current)
    _current_signal = current

    event = {
        "previousSignal": previous,
        "signal": current,
        "transition": transition,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    if transition != "UNCHANGED":
        logger.info("[ESP32] Presence transition: %s (%s -> %s)", transition, previous, current)
        for queue in list(_subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    return event


@router.get("/presence/stream")
async def stream_esp32_presence(request: Request) -> StreamingResponse:
    queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=32)
    _subscribers.add(queue)

    async def event_generator():
        try:
            initial = {
                "signal": _current_signal,
                "transition": "UNCHANGED",
                "timestamp": datetime.now(UTC).isoformat(),
            }
            yield f"event: presence_init\ndata: {json.dumps(initial)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"event: presence_transition\ndata: {json.dumps(event)}\n\n"
                except TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            _subscribers.discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
