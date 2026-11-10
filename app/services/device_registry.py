"""Local, durable device registry used on the MQTT data path.

The registry is deliberately a snapshot cache: sensor routing must keep working
when Java Cloud is unavailable and must never perform HTTP per MQTT message.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import bcrypt

from app.core.config import Settings


@dataclass(frozen=True)
class RegistryDevice:
    device_code: str
    device_type: str
    lifecycle_status: str
    credential_hash: str | None
    paired_kiosk_code: str | None
    desired_config: str
    desired_config_version: int


class DeviceRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._path = Path(settings.FOG_REGISTRY_CACHE_PATH)
        self._version = 0
        self._devices: dict[str, RegistryDevice] = {}
        self._gateway_lifecycle_status = "ACTIVE"
        self._invalidated_device_codes: set[str] = set()
        self._heartbeats: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def version(self) -> int:
        return self._version

    @property
    def gateway_active(self) -> bool:
        return self._gateway_lifecycle_status == "ACTIVE"

    def take_invalidated_device_codes(self) -> set[str]:
        codes, self._invalidated_device_codes = self._invalidated_device_codes, set()
        return codes

    def kiosk_for_sensor(self, sensor_code: str) -> str | None:
        if not self.gateway_active:
            return None
        sensor = self._devices.get(sensor_code)
        if sensor is None or sensor.device_type != "ESP32":
            return None
        if sensor.lifecycle_status != "ACTIVE":
            return None
        return sensor.paired_kiosk_code

    def active(self, device_code: str) -> RegistryDevice | None:
        if not self.gateway_active:
            return None
        device = self._devices.get(device_code)
        return device if device and device.lifecycle_status == "ACTIVE" else None

    def authenticate(self, device_code: str, password: str) -> bool:
        device = self.active(device_code)
        if device is None or not device.credential_hash:
            return False
        try:
            return bcrypt.checkpw(password.encode(), device.credential_hash.encode())
        except (ValueError, TypeError):
            return False

    def record_heartbeat(self, device_code: str, payload: dict[str, Any]) -> None:
        if self.active(device_code) is not None:
            self._heartbeats[device_code] = {"deviceCode": device_code, **payload}

    async def load(self) -> None:
        if not self._path.exists():
            return
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        self._replace(payload)

    async def sync(self) -> bool:
        if not self._settings.FOG_GATEWAY_DEVICE_CODE:
            return False
        auth = (self._settings.FOG_GATEWAY_DEVICE_CODE, self._settings.FOG_GATEWAY_DEVICE_SECRET)
        request = {"knownRegistryVersion": self._version, "heartbeats": list(self._heartbeats.values())}
        try:
            async with httpx.AsyncClient(timeout=8.0, auth=auth) as client:
                response = await client.post(
                    f"{self._settings.JAVA_CLOUD_BASE_URL.rstrip('/')}/api/v1/internal/fog/device-sync",
                    json=request,
                )
                response.raise_for_status()
        except httpx.HTTPError:
            return False
        payload = response.json()
        if payload.get("registryChanged"):
            async with self._lock:
                self._replace(payload)
                self._persist()
        else:
            self._version = int(payload.get("registryVersion", self._version))
            self._apply_gateway_lifecycle(payload.get("gatewayLifecycleStatus"))
        self._heartbeats.clear()
        return True

    def _replace(self, payload: dict[str, Any]) -> None:
        self._version = int(payload.get("registryVersion", 0))
        registry = payload.get("devices")
        if registry is None:
            raise ValueError("Changed registry response must include devices")
        next_devices = {
            item["deviceCode"]: RegistryDevice(
                device_code=item["deviceCode"], device_type=item["deviceType"],
                lifecycle_status=item["lifecycleStatus"], credential_hash=item.get("credentialHash"),
                paired_kiosk_code=item.get("pairedKioskCode"),
                desired_config=item.get("desiredConfig") or "{}",
                desired_config_version=int(item.get("desiredConfigVersion", 0)),
            ) for item in registry
        }
        for code, previous in self._devices.items():
            current = next_devices.get(code)
            if current is None or current.lifecycle_status != "ACTIVE" or current.credential_hash != previous.credential_hash:
                self._invalidated_device_codes.add(code)
        self._devices = next_devices
        self._apply_gateway_lifecycle(payload.get("gatewayLifecycleStatus"))

    def _apply_gateway_lifecycle(self, status: Any) -> None:
        next_status = str(status or "ACTIVE")
        if self._gateway_lifecycle_status == "ACTIVE" and next_status != "ACTIVE":
            self._invalidated_device_codes.update(self._devices)
        self._gateway_lifecycle_status = next_status

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        snapshot = {"registryVersion": self._version, "gatewayLifecycleStatus": self._gateway_lifecycle_status, "devices": [
            {"deviceCode": d.device_code, "deviceType": d.device_type,
             "lifecycleStatus": d.lifecycle_status, "credentialHash": d.credential_hash,
             "pairedKioskCode": d.paired_kiosk_code, "desiredConfig": d.desired_config,
             "desiredConfigVersion": d.desired_config_version} for d in self._devices.values()
        ]}
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(snapshot, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, self._path)
