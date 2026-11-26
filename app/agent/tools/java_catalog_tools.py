"""Shared Java API helpers for deterministic catalogue-backed agent tools."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from app.clients.java_catalog_client import JavaCatalogClient, JavaCatalogError
from app.core.config import settings


def java_catalog_client() -> JavaCatalogClient:
    """Build a short-lived client so FunctionTools remain safe in sync ADK calls."""
    return JavaCatalogClient(
        settings.JAVA_CLOUD_BASE_URL,
        settings.JAVA_REQUEST_TIMEOUT_SECONDS,
        settings.JAVA_AGENT_API_TOKEN,
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


def tool_result(query: str, resources: dict[str, Any], message: str | None = None) -> dict[str, Any]:
    """Return a compact, LLM-friendly success/failure contract for every Java tool."""
    result: dict[str, Any] = {"source": "java-api", "query": query, "resources": resources}
    if message:
        result["message"] = message
    return result


def tool_error(query: str, exc: JavaCatalogError) -> dict[str, Any]:
    message = "Không thể lấy dữ liệu từ hệ thống quản lý lớp học. Vui lòng thử lại sau."
    if exc.status_code == 401:
        message = "Dịch vụ agent chưa được cấu hình quyền truy cập dữ liệu Java."
    elif exc.status_code == 403:
        message = "Tài khoản dịch vụ agent chưa có quyền đọc dữ liệu này."
    return {"source": "java-api", "query": query, "error": message, "status_code": exc.status_code}


__all__ = [
    "calendar_window", "filter_by_query", "get_resource", "list_resource", "tool_error",
    "tool_result",
]
