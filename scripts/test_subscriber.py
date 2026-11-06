import asyncio
import sys
from pathlib import Path

# Thêm thư mục gốc vào sys.path để import được app khi chạy trực tiếp file script
sys.path.append(str(Path(__file__).resolve().parent.parent))

from amqtt.client import MQTTClient
from app.config import load_settings


async def main() -> None:
    settings = load_settings()
    client = MQTTClient()
    await client.connect(settings.internal_broker_url)
    await client.subscribe([(settings.mobile_output_topic, settings.mqtt_qos)])
    print(
        f"Subscribed to {settings.mobile_output_topic} on {settings.internal_broker_url}; waiting for one event..."
    )
    try:
        message = await client.deliver_message()
        if message is not None:
            print(bytes(message.data).decode("utf-8"))
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
