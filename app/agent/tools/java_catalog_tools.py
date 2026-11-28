"""Shared Java API helpers for deterministic catalogue-backed agent tools."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

from app.clients.java_catalog_client import JavaCatalogClient, JavaCatalogError
from app.core.config import settings
from app.core.tenant_context import get_current_tenant_context


def java_catalog_client() -> JavaCatalogClient:
    """Build a short-lived client so FunctionTools remain safe in sync ADK calls."""
    ctx = get_current_tenant_context()
    effective_bearer = (
        settings.JAVA_AGENT_API_TOKEN.strip()
        or (ctx.user_access_token.strip() if ctx is not None else "")
    )
    return JavaCatalogClient(
        settings.JAVA_CLOUD_BASE_URL,
        settings.JAVA_REQUEST_TIMEOUT_SECONDS,
        effective_bearer,
        tenant_id=ctx.tenant_id if ctx is not None else None,
        internal_service_token=settings.INTERNAL_GATEWAY_TOKEN,
    )


def list_resource(path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    client = java_catalog_client()
    try:
        return client.get_page(f"/api/v1{path}", params)
    finally:
        client.close()


def get_resource(path: str) -> Any:
    client = java_catalog_client()
    try:
        return client.get(f"/api/v1{path}")
    finally:
        client.close()


def calendar_window() -> dict[str, str]:
    start = datetime.now(tz=UTC).date()
    return {"fromDate": start.isoformat(), "toDate": (start + timedelta(days=60)).isoformat()}


def filter_by_query(items: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return items
    matched = [
        item
        for item in items
        if all(term in json.dumps(item, ensure_ascii=False).casefold() for term in terms)
    ]
    return matched or items


from pydantic import BaseModel


class CatalogToolResponse(BaseModel):
    """Uniform response contract for Java catalog tools."""

    source: str = "java-api"
    query: str
    resources: dict[str, Any] | None = None
    message: str | None = None
    error: str | None = None
    status_code: int | None = None


def tool_result(query: str, resources: dict[str, Any], message: str | None = None) -> dict[str, Any]:
    """Return a compact, LLM-friendly success/failure contract for every Java tool."""
    return CatalogToolResponse(
        query=query,
        resources=resources,
        message=message or None,
    ).model_dump(exclude_none=True)


def tool_error(query: str, exc: JavaCatalogError) -> dict[str, Any]:
    ctx = get_current_tenant_context()
    logger.warning(
        "JAVA_CATALOG_TOOL_ERROR | path=%s | statusCode=%s | query=%r | tenantId=%s | hasAgentToken=%s",
        exc.path,
        exc.status_code,
        query,
        ctx.tenant_id if ctx else None,
        bool(settings.JAVA_AGENT_API_TOKEN.strip()),
    )
    message = "Không thể lấy dữ liệu từ hệ thống quản lý lớp học. Vui lòng thử lại sau."
    if exc.status_code == 401:
        message = "Dịch vụ agent chưa được cấu hình quyền truy cập dữ liệu Java."
    elif exc.status_code == 403:
        message = "Tài khoản dịch vụ agent chưa có quyền đọc dữ liệu này."
    return CatalogToolResponse(
        query=query,
        error=message,
        status_code=exc.status_code,
    ).model_dump(exclude_none=True)


__all__ = [
    "CatalogToolResponse", "calendar_window", "filter_by_query", "get_resource", "list_resource",
    "tool_error", "tool_result",
]
