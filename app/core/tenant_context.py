"""Trusted multi-tenant request context and internal gateway verification.

Never trust tenantId/userId from LLM tool arguments or unauthenticated client bodies.
The Java gateway authenticates the user JWT, resolves the active tenant, and forwards
verified context headers to Python.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import Header, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import settings


class TrustedTenantContext(BaseModel, frozen=True):
    """Authenticated tenant + user context propagated from the Java gateway."""

    tenant_id: str = Field(min_length=1, max_length=100)
    user_id: str = Field(min_length=1, max_length=100)
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    user_access_token: str = ""
    source: str = "java-gateway"

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


_CURRENT_TENANT_CTX: ContextVar[TrustedTenantContext | None] = ContextVar(
    "current_trusted_tenant_context", default=None
)


def get_current_tenant_context() -> TrustedTenantContext | None:
    return _CURRENT_TENANT_CTX.get()


def require_tenant_context() -> TrustedTenantContext:
    ctx = _CURRENT_TENANT_CTX.get()
    if ctx is None or not ctx.tenant_id.strip():
        raise ValueError("Trusted tenant context is required but missing.")
    return ctx


def require_tenant_id(explicit_tenant_id: str | None = None) -> str:
    """Resolve tenant_id from trusted context; reject missing or mismatched tenant."""
    ctx = _CURRENT_TENANT_CTX.get()
    if ctx is not None:
        if explicit_tenant_id and explicit_tenant_id.strip() != ctx.tenant_id:
            raise PermissionError(
                "Cross-tenant access denied: explicit tenant_id does not match authenticated context."
            )
        return ctx.tenant_id
    if explicit_tenant_id and explicit_tenant_id.strip():
        return explicit_tenant_id.strip()
    raise ValueError("tenant_id is required; global/unscoped queries are disabled.")


@contextmanager
def tenant_context_scope(ctx: TrustedTenantContext) -> Iterator[TrustedTenantContext]:
    token = _CURRENT_TENANT_CTX.set(ctx)
    try:
        yield ctx
    finally:
        _CURRENT_TENANT_CTX.reset(token)


def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    parts = authorization.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].casefold() == "bearer":
        return parts[1].strip()
    return ""


async def require_internal_tenant_context(
    authorization: str | None = Header(default=None),
    x_internal_service_token: str | None = Header(default=None, alias="X-Internal-Service-Token"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_user_roles: str | None = Header(default=None, alias="X-User-Roles"),
    x_user_permissions: str | None = Header(default=None, alias="X-User-Permissions"),
    x_user_access_token: str | None = Header(default=None, alias="X-User-Access-Token"),
) -> TrustedTenantContext:
    """FastAPI dependency enforcing internal gateway auth and mandatory X-Tenant-Id (fail-closed)."""
    expected_token = settings.INTERNAL_GATEWAY_TOKEN.strip()
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal gateway authentication is not configured (INTERNAL_GATEWAY_TOKEN missing).",
        )
    provided = (x_internal_service_token or "").strip() or _extract_bearer(authorization)
    if not provided or not hmac.compare_digest(provided, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing internal gateway credentials.",
        )

    tenant_id = (x_tenant_id or "").strip()
    user_id = (x_user_id or "").strip()
    if not tenant_id or not user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authenticated X-Tenant-Id and X-User-Id headers from gateway are required.",
        )

    roles = tuple(r.strip() for r in (x_user_roles or "").split(",") if r.strip())
    permissions = tuple(p.strip() for p in (x_user_permissions or "").split(",") if p.strip())
    return TrustedTenantContext(
        tenant_id=tenant_id,
        user_id=user_id,
        roles=roles,
        permissions=permissions,
        user_access_token=(x_user_access_token or "").strip(),
    )


__all__ = [
    "TrustedTenantContext",
    "get_current_tenant_context",
    "require_internal_tenant_context",
    "require_tenant_context",
    "require_tenant_id",
    "tenant_context_scope",
]
