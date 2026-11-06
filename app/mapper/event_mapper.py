from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


def normalize_sensor_event(topic: str, payload: bytes) -> dict[str, Any] | None:
    """Convert an ESP32 sensor message to the mobile event contract."""
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != "esp32" or not parts[1] or parts[2] != "sensor":
        logger.warning("[Relay] Ignoring unexpected topic: %s", topic)
        return None

    raw_payload = payload.decode("utf-8", errors="replace")
    data: dict[str, Any]
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        data = {"raw": raw_payload}
    else:
        if isinstance(parsed, dict):
            data = dict(parsed)
        else:
            data = {"value": parsed}

    data["deviceId"] = parts[1]
    return {
        "type": "SENSOR_UPDATE",
        "eventId": str(uuid4()),
        "occurredAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "data": data,
    }
