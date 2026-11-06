from amqtt.broker import Broker

from app.core.config import Settings


def create_broker(settings: Settings) -> Broker:
    """Create one broker with TCP MQTT and MQTT-over-WebSocket listeners."""
    config = {
        "listeners": {
            "default": {
                "type": "tcp",
                "bind": f"{settings.MQTT_TCP_HOST}:{settings.MQTT_TCP_PORT}",
            },
            "websocket": {
                "type": "ws",
                "bind": f"{settings.MQTT_WS_HOST}:{settings.MQTT_WS_PORT}",
            },
        },
        "plugins": {
            "amqtt.plugins.authentication.AnonymousAuthPlugin": {
                "allow_anonymous": True,
            },
        },
    }
    return Broker(config)
