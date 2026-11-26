"""Synchronous read-only adapter for the Java catalogue APIs used by ADK tools."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx


class JavaCatalogError(RuntimeError):
    """A safe, user-facing failure from the Java catalogue service."""

    def __init__(self, path: str, status_code: int | None = None) -> None:
        self.path = path
        self.status_code = status_code
        super().__init__(f"Unable to read Java catalogue resource: {path}")


class JavaCatalogClient:
    """Small, stateless HTTP client for read-only Java catalogue endpoints."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        bearer_token: str = "",
        client: httpx.Client | None = None,
    ) -> None:
        headers = {"Accept": "application/json"}
        if bearer_token.strip():
            headers["Authorization"] = f"Bearer {bearer_token.strip()}"
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds, headers=headers
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise JavaCatalogError(path, exc.response.status_code) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise JavaCatalogError(path) from exc

    def get_page(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload = self.get(path, {"page": 0, "size": 100, **(params or {})})
        if isinstance(payload, dict) and isinstance(payload.get("content"), list):
            return [item for item in payload["content"] if isinstance(item, dict)]
        return payload if isinstance(payload, list) else []

    def get_many(self, paths: Iterable[str]) -> dict[str, Any]:
        return {path: self.get(path) for path in paths}

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["JavaCatalogClient", "JavaCatalogError"]
