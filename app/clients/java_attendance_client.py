from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx


class JavaCloudError(RuntimeError):
    def __init__(self, operation: str, status_code: int | None = None) -> None:
        self.operation = operation
        self.status_code = status_code
        super().__init__(f"Java Cloud operation failed: {operation}")


class JavaAttendanceClient:
    """HTTP adapter for the small Fog-to-Java face scan interface."""

    def __init__(
        self,
        base_url: str,
        fog_secret: str,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            headers={"X-Fog-Secret": fog_secret},
        )

    async def validate_upload(
        self, request_id: UUID, device_id: str, upload_ticket: str
    ) -> None:
        await self._post(
            request_id,
            "validate-upload",
            {"deviceId": device_id, "uploadTicket": upload_ticket},
        )

    async def report_stage(
        self,
        request_id: UUID,
        device_id: str,
        stage: str,
        error: dict[str, str] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"deviceId": device_id, "stage": stage}
        if error is not None:
            payload["error"] = error
        await self._post(request_id, "events", payload)

    async def submit_embedding(
        self, request_id: UUID, device_id: str, embedding: list[float]
    ) -> None:
        await self._post(
            request_id,
            "embedding",
            {"deviceId": device_id, "embedding": embedding},
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _post(
        self, request_id: UUID, operation: str, payload: dict[str, Any]
    ) -> None:
        try:
            response = await self._client.post(
                f"/api/v1/internal/face-check-in-scans/{request_id}/{operation}",
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise JavaCloudError(operation, exc.response.status_code) from exc
        except httpx.HTTPError as exc:
            raise JavaCloudError(operation) from exc
