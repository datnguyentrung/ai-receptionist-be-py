import json
import time
from types import SimpleNamespace

import pytest

from app.schemas.mqtt_relay import MqttRelay


class RecordingMqttClient:
    def __init__(self) -> None:
        self.messages: list[tuple] = []

    async def publish(self, topic, payload, qos, retain):
        self.messages.append((topic, json.loads(payload), qos, retain))


def settings(mapping=None, debounce=5.0):
    mapping = mapping or {"esp32-01": "kiosk-01"}
    return SimpleNamespace(
        sensor_kiosk_map=mapping,
        MOTION_DEBOUNCE_SECONDS=debounce,
        MQTT_QOS=1,
        mobile_command_topic=lambda device_id: f"taekwondo/kiosks/{device_id}/commands",
    )


@pytest.mark.asyncio
async def test_motion_routes_only_to_mapped_kiosk() -> None:
    async def ready():
        return True

    relay = MqttRelay(settings(), readiness_probe=ready)
    client = RecordingMqttClient()
    relay._client = client

    await relay._process_message("esp32/esp32-01/sensor", b'{"motion":true}')

    assert len(client.messages) == 1
    topic, payload, qos, retain = client.messages[0]
    assert topic == "taekwondo/kiosks/kiosk-01/commands"
    assert payload["type"] == "CHECK_IN_OPEN"
    assert payload["deviceId"] == "kiosk-01"
    assert payload["sensorDeviceId"] == "esp32-01"
    assert qos == 1
    assert retain is False


@pytest.mark.asyncio
async def test_motion_end_closes_check_in_for_the_mapped_kiosk() -> None:
    async def not_ready():
        return False

    relay = MqttRelay(settings(), readiness_probe=not_ready)
    client = RecordingMqttClient()
    relay._client = client

    await relay._process_message("esp32/esp32-01/sensor", b'{"motion":false}')

    assert len(client.messages) == 1
    topic, payload, qos, retain = client.messages[0]
    assert topic == "taekwondo/kiosks/kiosk-01/commands"
    assert payload["type"] == "CHECK_IN_CLOSE"
    assert payload["deviceId"] == "kiosk-01"
    assert payload["sensorDeviceId"] == "esp32-01"
    assert qos == 1
    assert retain is False


@pytest.mark.asyncio
async def test_new_motion_after_a_close_reopens_check_in_without_debounce_delay() -> None:
    async def ready():
        return True

    relay = MqttRelay(settings(debounce=60.0), readiness_probe=ready)
    client = RecordingMqttClient()
    relay._client = client

    await relay._process_message("esp32/esp32-01/sensor", b'{"motion":true}')
    await relay._process_message("esp32/esp32-01/sensor", b'{"motion":false}')
    await relay._process_message("esp32/esp32-01/sensor", b'{"motion":true}')

    assert [payload["type"] for _, payload, _, _ in client.messages] == [
        "CHECK_IN_OPEN",
        "CHECK_IN_CLOSE",
        "CHECK_IN_OPEN",
    ]


@pytest.mark.asyncio
async def test_stale_sensor_heartbeat_closes_check_in_once() -> None:
    async def ready():
        return True

    relay = MqttRelay(settings(), readiness_probe=ready)
    relay._heartbeat_timeout_seconds = 1.0
    relay._last_sensor_seen_at["esp32-01"] = time.monotonic() - 1.0
    client = RecordingMqttClient()
    relay._client = client

    await relay._close_stale_sensor_check_ins()
    await relay._close_stale_sensor_check_ins()

    assert len(client.messages) == 1
    _, payload, _, _ = client.messages[0]
    assert payload["type"] == "CHECK_IN_CLOSE"
    assert payload["reason"] == "SENSOR_OFFLINE"


@pytest.mark.asyncio
async def test_motion_is_debounced_and_requires_ready_model() -> None:
    async def ready():
        return True

    async def not_ready_probe():
        return False

    relay = MqttRelay(settings(), readiness_probe=ready)
    client = RecordingMqttClient()
    relay._client = client
    await relay._process_message("esp32/esp32-01/sensor", b'{"motion":true}')
    await relay._process_message("esp32/esp32-01/sensor", b'{"motion":true}')
    assert len(client.messages) == 1

    not_ready = MqttRelay(settings(), readiness_probe=not_ready_probe)
    not_ready._client = client
    await not_ready._process_message("esp32/esp32-01/sensor", b'{"motion":true}')
    assert len(client.messages) == 1


@pytest.mark.asyncio
async def test_unmapped_sensor_is_ignored() -> None:
    async def ready():
        return True

    relay = MqttRelay(
        settings(mapping={"other": "kiosk-02"}), readiness_probe=ready
    )
    client = RecordingMqttClient()
    relay._client = client
    await relay._process_message("esp32/esp32-01/sensor", b'{"motion":true}')
    assert client.messages == []
