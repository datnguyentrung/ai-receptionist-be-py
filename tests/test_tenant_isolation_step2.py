"""Tests for Step 2: Mandatory tenant_id isolation, no global fallback, and UNWIND-safe Neo4j writes."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.agent.tools.graphrag_tools import execute_read_cypher, retrieve_scope_knowledge
from app.core.tenant_context import (
    TrustedTenantContext,
    require_internal_tenant_context,
    require_tenant_id,
    tenant_context_scope,
)
from app.schemas.knowledge import KnowledgeFactRecord
from app.services.graphrag.graph_store import Neo4jGraphStore
from app.services.graphrag.retrieval import GraphQaRequest
from app.services.knowledge.knowledge_store import TenantKnowledgeStore


def _mock_neo4j_driver():
    captured: list[tuple[str, dict]] = []

    class _FakeResult:
        async def single(self):
            return {"entityKey": "k1"}

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def run(self, query, **kwargs):
            captured.append((query, kwargs))
            return _FakeResult()

    driver = MagicMock()
    driver.session.return_value = _FakeSession()
    return driver, captured


@pytest.mark.anyio
async def test_neo4j_graph_store_rejects_empty_tenant_and_removes_is_null():
    driver, captured = _mock_neo4j_driver()
    store = Neo4jGraphStore(driver, "neo4j", embedding_provider=MagicMock())

    for call in (
        store.search_chunk_vector([0.1, 0.2], 5, tenant_id=""),
        store.search_chunk_fulltext("taekwondo", 5, tenant_id="   "),
        store.search_entity_fulltext("taekwondo", 5, tenant_id=""),
        store.search_fact_vector([0.1, 0.2], 5, tenant_id=""),
        store.chunks_for_entities(["k1"], 5, tenant_id=""),
        store.chunks_by_ids(["c1"], tenant_id=""),
        store.document_history("doc-1", tenant_id=""),
    ):
        with pytest.raises(ValueError, match="tenant_id is required"):
            await call

    await store.search_chunk_vector([0.1, 0.2], 5, tenant_id="tenant-a")
    await store.chunks_for_entities(["k1"], 5, tenant_id="tenant-a")
    await store.chunks_by_ids(["c1"], tenant_id="tenant-a")
    await store.document_history("doc-1", tenant_id="tenant-a")

    assert len(captured) == 4
    for cypher, params in captured:
        assert "$tenant_id IS NULL" not in cypher
        assert "tenantId: $tenant_id" in cypher or "tenantId = $tenant_id" in cypher
        assert params["tenant_id"] == "tenant-a"


def test_graph_qa_request_requires_tenant_id():
    with pytest.raises(ValidationError):
        GraphQaRequest(question="Học phí bao nhiêu?")
    with pytest.raises(ValidationError):
        GraphQaRequest(question="Học phí bao nhiêu?", tenant_id="")


@pytest.mark.anyio
async def test_knowledge_store_uses_foreach_safe_with_empty_business_refs_or_facts():
    driver, captured = _mock_neo4j_driver()
    store = TenantKnowledgeStore(driver, "neo4j")

    # Empty business_refs must still persist facts
    res = await store.upsert_fact_record(
        KnowledgeFactRecord(
            tenant_id="tenant-a",
            scope_key="training_knowledge",
            entity_type="poomsae",
            entity_key="tenant-a:poomsae:taegeuk1",
            properties={"name": "Taegeuk 1"},
            business_refs=[],
            fact_statements=["Bài quyền số 1 có 18 động tác."],
        )
    )
    assert res["facts"] == 1
    cypher, params = captured[0]
    assert "UNWIND $business_refs" not in cypher
    assert "FOREACH (bref IN $business_refs" in cypher
    assert "FOREACH (f IN $facts" in cypher
    assert params["business_refs"] == []
    assert len(params["facts"]) == 1

    # Empty tenant_id must fail
    with pytest.raises(ValueError, match="tenant_id is required"):
        await store.upsert_fact_record(
            KnowledgeFactRecord(
                tenant_id="",
                scope_key="training_knowledge",
                entity_type="poomsae",
                entity_key="k",
                properties={},
            )
        )


@pytest.mark.anyio
async def test_internal_gateway_auth_and_cross_tenant_spoof_blocked(monkeypatch):
    monkeypatch.setattr("app.core.tenant_context.settings.INTERNAL_GATEWAY_TOKEN", "secret-gw-token")

    # Wrong token -> 401
    with pytest.raises(HTTPException) as exc_401:
        await require_internal_tenant_context(
            authorization="Bearer wrong",
            x_internal_service_token=None,
            x_tenant_id="tenant-a",
            x_user_id="user-1",
            x_user_roles=None,
            x_user_permissions=None,
        )
    assert exc_401.value.status_code == 401

    # Missing tenant -> 403
    with pytest.raises(HTTPException) as exc_403:
        await require_internal_tenant_context(
            authorization="Bearer secret-gw-token",
            x_internal_service_token=None,
            x_tenant_id="",
            x_user_id="user-1",
            x_user_roles=None,
            x_user_permissions=None,
        )
    assert exc_403.value.status_code == 403

    # Valid gateway headers -> TrustedTenantContext
    ctx = await require_internal_tenant_context(
        authorization=None,
        x_internal_service_token="secret-gw-token",
        x_tenant_id="tenant-a",
        x_user_id="admin-1",
        x_user_roles="ADMIN",
        x_user_permissions="KNOWLEDGE_PUBLISH",
    )
    assert ctx.tenant_id == "tenant-a"
    assert ctx.has_permission("KNOWLEDGE_PUBLISH")

    # Attempting to spoof a different tenant_id inside context raises PermissionError
    with tenant_context_scope(ctx):
        assert require_tenant_id() == "tenant-a"
        with pytest.raises(PermissionError, match="Cross-tenant"):
            require_tenant_id("tenant-b")


@pytest.mark.anyio
async def test_graphrag_tools_fail_closed_without_tenant_context():
    res_cypher = await execute_read_cypher("MATCH (n) RETURN n LIMIT 5")
    assert res_cypher["success"] is False
    assert res_cypher["errorCode"] == "CYPHER_FREEFORM_DISABLED"

    res_scope = await retrieve_scope_knowledge("chính sách học thử", "admission_onboarding")
    assert res_scope["status"] == "UNAVAILABLE"
