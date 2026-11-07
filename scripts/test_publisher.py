import asyncio
import json
import sys
from pathlib import Path

# Thêm thư mục gốc vào sys.path để import được app khi chạy trực tiếp file script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from amqtt.client import MQTTClient

from app.core.config import settings


async def main() -> None:
    # Nếu topic có wildcard '+' (ví dụ esp32/+/sensor) thì thay bằng deviceId cụ thể để publish test
    publish_topic = settings.ESP32_INPUT_TOPIC.replace("+", "esp32-01")

    client = MQTTClient()
    await client.connect(settings.internal_broker_url)
    try:
        payload = {"temperature": 29.5, "humidity": 74}
        await client.publish(
            publish_topic,
            json.dumps(payload).encode("utf-8"),
            qos=settings.MQTT_QOS,
            retain=False,
        )
        print(
            f"Published to {publish_topic} via {settings.internal_broker_url}:",
            json.dumps(payload),
        )
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
