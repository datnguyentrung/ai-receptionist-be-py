import asyncio
import contextlib
import json
import logging

from amqtt.client import ClientError, MQTTClient

from app.core.config import Settings
from app.mapper.event_mapper import normalize_sensor_event

logger = logging.getLogger(__name__)


class MqttRelay:
    """Subscribes only to ESP32 data and republishes normalized mobile events."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: MQTTClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

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

            if self._client is None:
                raise ClientError("MQTT relay client is not connected")
            encoded_event = json.dumps(
                event, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            await self._client.publish(
                self._settings.MOBILE_OUTPUT_TOPIC,
                encoded_event,
                qos=self._settings.mqtt_qos,
                retain=False,
            )
            logger.info(
                "[Relay] published topic=%s eventId=%s",
                self._settings.MOBILE_OUTPUT_TOPIC,
                event["eventId"],
            )
        except Exception:
            logger.exception("[Relay] Failed to process message on %s", topic)

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(ClientError, OSError, ConnectionError):
                await client.disconnect()
