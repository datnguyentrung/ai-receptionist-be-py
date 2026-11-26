"""aMQTT authentication and ACL plugins backed by the immutable fog registry.

The registry reference is injected once during application startup.  Plugins never
perform network I/O, which keeps the MQTT data path available during a Java outage.
"""
from __future__ import annotations

import hmac
import logging
from typing import TYPE_CHECKING

from amqtt.contexts import Action
from amqtt.plugins.base import BaseAuthPlugin, BaseTopicPlugin
from amqtt.session import Session

if TYPE_CHECKING:
    from app.services.device_registry import DeviceRegistry

_registry: DeviceRegistry | None = None
_relay_username: str | None = None
_relay_secret: str | None = None
_relay_topics: tuple[str, ...] = ()
logger = logging.getLogger(__name__)


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
    """Authenticate ACTIVE devices and PROVISIONED devices' first heartbeat."""

    async def authenticate(self, *, session: Session) -> bool:
        if _is_relay(session):
            return True
        if _registry is None or not session.username or not session.password:
            logger.warning("[Registry] MQTT authentication rejected | reason=missing_registry_or_credentials")
            return False
        client_id = getattr(session, "client_id", None)
        if client_id != session.username:
            logger.warning("[Registry] MQTT authentication rejected | deviceCode=%s | reason=client_id_mismatch", session.username)
            return False
        authenticated = _registry.authenticate(session.username, session.password)
        if not authenticated:
            logger.warning(
                "[Registry] MQTT authentication rejected | deviceCode=%s | reason=%s",
                session.username,
                _registry.connection_rejection_reason(session.username) or "invalid_credential",
            )
        return authenticated


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
        device = _registry.connected(session.username)
        if device is None:
            logger.warning(
                "[Registry] MQTT topic rejected | deviceCode=%s | reason=%s",
                session.username,
                _registry.connection_rejection_reason(session.username) or "unknown",
            )
            return False
        code = device.device_code
        heartbeat = f"taekwondo/devices/{code}/heartbeat"
        if device.lifecycle_status == "PROVISIONED":
            allowed = action == Action.PUBLISH and topic == heartbeat
            if not allowed:
                logger.warning("[Registry] MQTT topic rejected | deviceCode=%s | reason=provisioned_heartbeat_only", code)
            return allowed
        if device.device_type == "ESP32":
            return action == Action.PUBLISH and topic in {heartbeat, f"esp32/{code}/sensor"}
        if device.device_type == "KIOSK":
            if action == Action.PUBLISH:
                return topic == heartbeat
            if action in {Action.SUBSCRIBE, Action.RECEIVE}:
                return topic == f"taekwondo/kiosks/{code}/commands"
        return False
