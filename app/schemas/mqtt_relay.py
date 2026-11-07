import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable

from amqtt.client import ClientError, MQTTClient

from app.core.config import Settings
from app.mapper.event_mapper import build_check_in_open_event, normalize_sensor_event

logger = logging.getLogger(__name__)


class MqttRelay:
    """Subscribes only to ESP32 data and republishes normalized mobile events."""

    def __init__(
        self,
        settings: Settings,
        readiness_probe: Callable[[], Awaitable[bool]],
    ) -> None:
        self._settings = settings
        self._readiness_probe = readiness_probe
        self._client: MQTTClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._last_trigger_at: dict[str, float] = {}

    async def start(self) -> None:
        await self._connect_and_subscribe()
        self._task = asyncio.create_task(self._consume(), name="mqtt-relay")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._disconnect()

    async def _connect_and_subscribe(self) -> None:
        self._client = MQTTClient()
        await self._client.connect(self._settings.internal_broker_url)
        logger.info("[Relay] Connected to broker")
        await self._client.subscribe(
            [(self._settings.ESP32_INPUT_TOPIC, self._settings.MQTT_QOS)]
        )
        logger.info("[Relay] Subscribed: %s", self._settings.ESP32_INPUT_TOPIC)

    async def _consume(self) -> None:
        while not self._stopping.is_set():
            try:
                if self._client is None:
                    await self._connect_and_subscribe()

                client = self._client
                if client is None:
                    raise ClientError("MQTT relay client is not connected")

                message = await client.deliver_message()
                if message is not None:
                    await self._process_message(message.topic, bytes(message.data))
            except asyncio.CancelledError:
                raise
            except (ClientError, OSError):
                if not self._stopping.is_set():
                    logger.exception("[Relay] Connection lost; retrying in 2 seconds")
                    await self._disconnect()
                    await asyncio.sleep(2)
            except Exception:
                logger.exception("[Relay] Unexpected error while receiving MQTT data")

    async def _process_message(self, topic: str, payload: bytes) -> None:
        try:
            raw_payload = payload.decode("utf-8", errors="replace")
            logger.info("[ESP32] topic=%s", topic)
            logger.info("[ESP32] payload=%s", raw_payload)
            event = normalize_sensor_event(topic, payload)
            if event is None:
                return

            sensor_device_id = str(event["data"]["sensorDeviceId"])
            kiosk_device_id = self._settings.sensor_kiosk_map.get(sensor_device_id)
            if kiosk_device_id is None:
                logger.warning(
                    "[Relay] No kiosk mapping for sensor deviceId=%s",
                    sensor_device_id,
                )
                return

            now = time.monotonic()
            previous = self._last_trigger_at.get(sensor_device_id)
            if previous is not None and (
                now - previous < self._settings.MOTION_DEBOUNCE_SECONDS
            ):
                logger.info(
                    "[Relay] Debounced sensor deviceId=%s", sensor_device_id
                )
                return

            # This is the same readiness interface exposed by the warmup endpoint.
            # The hot model is never re-created in the relay.
            if not await self._readiness_probe():
                logger.error(
                    "[Relay] InsightFace is not ready; skipping sensor deviceId=%s",
                    sensor_device_id,
                )
                return

            self._last_trigger_at[sensor_device_id] = now
            command = build_check_in_open_event(event, kiosk_device_id)

            if self._client is None:
                raise ClientError("MQTT relay client is not connected")
            encoded_event = json.dumps(
                command, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            output_topic = self._settings.mobile_command_topic(kiosk_device_id)
            await self._client.publish(
                output_topic,
                encoded_event,
                qos=self._settings.MQTT_QOS,
                retain=False,
            )
            logger.info(
                "[Relay] published topic=%s eventId=%s kioskDeviceId=%s",
                output_topic,
                event["eventId"],
                kiosk_device_id,
            )
        except Exception:
            logger.exception("[Relay] Failed to process message on %s", topic)

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(ClientError, OSError, ConnectionError):
                await client.disconnect()
