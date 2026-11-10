"""Enroll and manage the local fog gateway credential.

Usage: python -m app.commands.provision_fog provision --java-url URL --code CODE
       python -m app.commands.provision_fog status
       python -m app.commands.provision_fog clear
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

DEFAULT_PATH = Path(".fog/gateway-credential.json")


def credential_path() -> Path:
    return Path(os.environ.get("FOG_GATEWAY_CREDENTIAL_PATH", DEFAULT_PATH))


def write_credential(payload: dict[str, str]) -> None:
    path = credential_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(payload, file)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows ACL deployment is owned by the service installer.
        pass


def provision(java_url: str, code: str) -> int:
    url = f"{java_url.rstrip('/')}/api/v1/device-provisioning/claim"
    try:
        response = httpx.post(url, json={"code": code, "deviceType": "FOG_GATEWAY", "installationId": os.uname().nodename if hasattr(os, "uname") else "fog-host"}, timeout=15.0)
        response.raise_for_status()
        claim = response.json()
    except httpx.HTTPError as exc:
        print(f"Fog provisioning failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    if not claim.get("deviceCode") or not claim.get("secret"):
        print("Fog provisioning response is incomplete", file=sys.stderr)
        return 1
    write_credential({"javaUrl": java_url.rstrip("/"), "deviceCode": claim["deviceCode"], "secret": claim["secret"]})
    print(f"Fog gateway {claim['deviceCode']} has been enrolled.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision a fog gateway")
    commands = parser.add_subparsers(dest="command", required=True)
    enroll = commands.add_parser("provision")
    enroll.add_argument("--java-url", required=True)
    enroll.add_argument("--code", required=True)
    commands.add_parser("status")
    commands.add_parser("clear")
    args = parser.parse_args()
    path = credential_path()
    if args.command == "provision":
        return provision(args.java_url, args.code)
    if args.command == "status":
        if not path.exists():
            print("Fog gateway is not provisioned.")
            return 1
        payload = json.loads(path.read_text(encoding="utf-8"))
        print(f"Fog gateway provisioned: {payload.get('deviceCode', 'unknown')}")
        return 0
    if path.exists():
        path.unlink()
    print("Fog gateway credential cleared.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
