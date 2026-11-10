"""aMQTT authentication and ACL plugins backed by the immutable fog registry.

The registry reference is injected once during application startup.  Plugins never
perform network I/O, which keeps the MQTT data path available during a Java outage.
"""
from __future__ import annotations

import hmac
from typing import TYPE_CHECKING

from amqtt.contexts import Action, BaseContext
from amqtt.plugins.base import BaseAuthPlugin, BaseTopicPlugin
from amqtt.session import Session

if TYPE_CHECKING:
    from app.services.device_registry import DeviceRegistry

_registry: DeviceRegistry | None = None
_relay_username: str | None = None
_relay_secret: str | None = None
_relay_topics: tuple[str, ...] = ()


def set_registry(registry: DeviceRegistry) -> None:
    global _registry
    _registry = registry


def set_relay_credential(username: str, secret: str, topics: tuple[str, ...]) -> None:
    """Register the process-local identity used by the embedded MQTT relay."""
    global _relay_username, _relay_secret, _relay_topics
    _relay_username = username
    _relay_secret = secret
    _relay_topics = topics


def _is_relay(session: Session) -> bool:
    return bool(
        _relay_username
        and _relay_secret
        and session.username == _relay_username
        and getattr(session, "client_id", None) == _relay_username
        and session.password
        and hmac.compare_digest(session.password, _relay_secret)
    )


def _matches_topic_filter(topic: str, topic_filter: str | None) -> bool:
    if not topic_filter:
        return False
    requested, allowed = topic.split("/"), topic_filter.split("/")
    for index, segment in enumerate(allowed):
        if segment == "#":
            return index == len(allowed) - 1
        if index >= len(requested) or (segment != "+" and segment != requested[index]):
            return False
    return len(requested) == len(allowed)


class RegistryAuthPlugin(BaseAuthPlugin):
    """Accept only an ACTIVE registry device using its device code and secret."""

    async def authenticate(self, *, session: Session) -> bool:
        if _is_relay(session):
            return True
        if _registry is None or not session.username or not session.password:
            return False
        client_id = getattr(session, "client_id", None)
        if client_id != session.username:
            return False
        return _registry.authenticate(session.username, session.password)


class RegistryTopicAclPlugin(BaseTopicPlugin):
    """Restrict each device to its own heartbeat/sensor/command topic."""

    async def topic_filtering(
        self,
        *,
        session: Session | None = None,
        topic: str | None = None,
        action: Action | None = None,
    ) -> bool:
        if session is None or not session.username or not topic:
            return False
        if _is_relay(session):
            if action == Action.SUBSCRIBE:
                return topic in _relay_topics
            if action == Action.RECEIVE:
                return any(_matches_topic_filter(topic, topic_filter) for topic_filter in _relay_topics)
            if action == Action.PUBLISH:
                return topic.startswith("taekwondo/kiosks/") and topic.endswith("/commands")
            return False
        if _registry is None:
            return False
        device = _registry.active(session.username)
        if device is None:
            return False
        code = device.device_code
        heartbeat = f"taekwondo/devices/{code}/heartbeat"
        if device.device_type == "ESP32":
            return action == Action.PUBLISH and topic in {heartbeat, f"esp32/{code}/sensor"}
        if device.device_type == "KIOSK":
            if action == Action.PUBLISH:
                return topic == heartbeat
            if action in {Action.SUBSCRIBE, Action.RECEIVE}:
                return topic == f"taekwondo/kiosks/{code}/commands"
        return False
