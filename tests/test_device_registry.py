from types import SimpleNamespace

import bcrypt

from app.services.device_registry import DeviceRegistry


def test_provisioned_device_can_authenticate_and_queue_its_first_heartbeat(tmp_path) -> None:
    """The first heartbeat is what promotes a claimed device to ACTIVE."""
    registry = DeviceRegistry(SimpleNamespace(FOG_REGISTRY_CACHE_PATH=str(tmp_path / "registry.json")))
    credential_hash = bcrypt.hashpw(b"device-secret", bcrypt.gensalt()).decode()
    registry._replace({
        "registryVersion": 1,
        "gatewayLifecycleStatus": "ACTIVE",
        "devices": [{
            "deviceCode": "kiosk-01",
            "deviceType": "KIOSK",
            "lifecycleStatus": "PROVISIONED",
            "credentialHash": credential_hash,
            "desiredConfig": "{}",
            "desiredConfigVersion": 0,
        }],
    })

    assert registry.authenticate("kiosk-01", "device-secret") is True
    registry.record_heartbeat("kiosk-01", {"sentAt": "2026-11-24T09:05:00Z"})

    assert registry._heartbeats == {
        "kiosk-01": {
            "deviceCode": "kiosk-01",
            "sentAt": "2026-11-24T09:05:00Z",
        }
    }


def test_reports_when_device_is_absent_from_the_fog_registry(tmp_path) -> None:
    registry = DeviceRegistry(SimpleNamespace(FOG_REGISTRY_CACHE_PATH=str(tmp_path / "registry.json")))

    assert registry.connection_rejection_reason("kiosk-missing") == "device_not_in_fog_registry"
