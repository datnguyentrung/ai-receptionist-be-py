"""Tests for Persistence, Neo4j Publish->GraphRAG, DOCX/MD Extraction, Fail-Closed Token, Strict RBAC, and Cypher Guard."""

import io
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.agent.tools.graphrag_tools import execute_read_cypher
from app.core.tenant_context import (
    TrustedTenantContext,
    require_internal_tenant_context,
    tenant_context_scope,
)
from app.services.knowledge.document_extractor import (
    extract_candidate_facts,
    extract_text_from_document,
)
from app.services.knowledge.knowledge_store import TenantKnowledgeStore
from app.services.knowledge.workflow_service import (
    KnowledgeItemStatus,
    KnowledgeSourceType,
    KnowledgeWorkflowService,
)


def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    buf = io.BytesIO()
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_docx_and_markdown_extraction_and_pdf_rejected():
    docx_bytes = _make_docx_bytes(
        [
            "Lịch sử trung tâm Taekwondo Văn Quán thành lập năm 2015.",
            "Bài quyền Taegeuk 1 gồm 18 động tác cơ bản.",
        ]
    )
    text = extract_text_from_document("gioi_thieu.docx", docx_bytes)
    assert "năm 2015" in text
    assert "Taegeuk 1" in text

    facts = extract_candidate_facts(
        tenant_id="tenant-a", scope_key="training_knowledge", raw_text=text
    )
    assert facts[0].entity_type == "center_profile"
    assert facts[1].entity_type == "poomsae"

    with pytest.raises(ValueError, match="Word"):
        extract_text_from_document("document.pdf", b"%PDF-1.4...")


@pytest.mark.anyio
async def test_fail_closed_internal_gateway_token(monkeypatch):
    monkeypatch.setattr("app.core.tenant_context.settings.INTERNAL_GATEWAY_TOKEN", "")
    with pytest.raises(HTTPException) as exc_503:
        await require_internal_tenant_context(
            authorization=None,
            x_internal_service_token=None,
            x_tenant_id="tenant-a",
            x_user_id="admin-1",
            x_user_roles="ADMIN",
            x_user_permissions="KNOWLEDGE_PUBLISH",
        )
    assert exc_503.value.status_code == 503


def test_strict_default_deny_rbac():
    wf = KnowledgeWorkflowService()
    admin = TrustedTenantContext(tenant_id="t1", user_id="a1", roles=("ADMIN",))
    no_claims = TrustedTenantContext(tenant_id="t1", user_id="u1", roles=(), permissions=())
    broad_staff = TrustedTenantContext(tenant_id="t1", user_id="s1", roles=("STAFF",), permissions=())
    reviewer = TrustedTenantContext(
        tenant_id="t1", user_id="r1", roles=(), permissions=("KNOWLEDGE_REVIEW",)
    )

    draft = wf.create_draft(
        admin,
        source_type=KnowledgeSourceType.RICH_TEXT,
        scope_key="center_overview",
        title="Giới thiệu",
        raw_text="Trung tâm Taekwondo Văn Quán đào tạo võ thuật chuyên nghiệp.",
    )
    wf.transition(admin, draft.item_id, KnowledgeItemStatus.PENDING_REVIEW)

    with pytest.raises(PermissionError):
        wf.transition(no_claims, draft.item_id, KnowledgeItemStatus.PUBLISHED)
    with pytest.raises(PermissionError):
        wf.transition(broad_staff, draft.item_id, KnowledgeItemStatus.PUBLISHED)

    # Reviewer can reject, but cannot publish without KNOWLEDGE_PUBLISH
    with pytest.raises(PermissionError):
        wf.transition(reviewer, draft.item_id, KnowledgeItemStatus.PUBLISHED)
    wf.transition(reviewer, draft.item_id, KnowledgeItemStatus.REJECTED)


@pytest.mark.anyio
async def test_publish_pushes_to_neo4j_graphrag_and_archive_supersedes():
    published_calls: list[str] = []
    archived_calls: list[str] = []

    async def fake_publish(record):
        published_calls.append(record.item_id)
        return {"versionId": record.item_id}

    async def fake_archive(tenant_id, item_id):
        archived_calls.append(f"{tenant_id}:{item_id}")
        return True

    wf = KnowledgeWorkflowService(graph_publisher=fake_publish, graph_archiver=fake_archive)
    admin = TrustedTenantContext(tenant_id="tenant-a", user_id="a1", roles=("ADMIN",))

    draft = await wf.acreate_draft(
        admin,
        source_type=KnowledgeSourceType.RICH_TEXT,
        scope_key="center_overview",
        title="Triết lý đào tạo",
        raw_text="Trung tâm Taekwondo rèn luyện kỷ luật và tinh thần thượng võ.",
    )
    await wf.atransition(admin, draft.item_id, KnowledgeItemStatus.PENDING_REVIEW)
    await wf.atransition(admin, draft.item_id, KnowledgeItemStatus.PUBLISHED)
    assert published_calls == [draft.item_id]


@pytest.mark.anyio
async def test_neo4j_publish_governance_item_writes_committed_version_and_chunks():
    captured: list[tuple[str, dict]] = []

    class _FakeResult:
        async def single(self):
            return {"versionId": "item-1"}

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
    embedder = MagicMock()
    embedder.embed_documents = AsyncMock(return_value=[[0.1, 0.2], [0.3, 0.4]])

    store = TenantKnowledgeStore(driver, "neo4j", embedding_provider=embedder)
    res = await store.publish_governance_item(
        tenant_id="tenant-a",
        item_id="item-1",
        title="Học thử",
        scope_key="admission_onboarding",
        facts=[{"fact_id": "f1", "entity_type": "policy_rule", "statement": "Học thử 1 buổi."}],
        visibility="PUBLIC",
        effective_from="2026-01-01",
        effective_to="2026-12-31",
    )
    assert res["versionId"] == "item-1"
    cypher, params = captured[0]
    assert "TaekwondoSourceVersion {versionId: $item_id, tenantId: $tenant_id}" in cypher
    assert "v.status = 'COMMITTED'" in cypher
    assert "v.visibility = $visibility" in cypher
    assert "v.effectiveFrom = $effective_from" in cypher
    assert "v.effectiveTo = $effective_to" in cypher
    assert "TaekwondoChunk" in cypher
    assert params["tenant_id"] == "tenant-a"
    # Sanitized chunk text contains ONLY accepted fact statements, never rejected raw prose
    assert params["sanitized_text"] == "Học thử 1 buổi."
    assert params["visibility"] == "PUBLIC"
    assert params["effective_from"] == "2026-01-01"
    assert params["effective_to"] == "2026-12-31"


@pytest.mark.anyio
async def test_execute_read_cypher_disabled_for_freeform_queries():
    with tenant_context_scope(TrustedTenantContext(tenant_id="tenant-a", user_id="u1")):
        res = await execute_read_cypher(
            "MATCH (n {tenantId: $tenant_id}) MATCH (b) RETURN b LIMIT 5"
        )
        assert res["success"] is False
        assert res["errorCode"] == "CYPHER_FREEFORM_DISABLED"


@pytest.mark.anyio
async def test_neo4j_graph_store_enforces_visibility_and_effective_dates():
    captured: list[tuple[str, dict]] = []

    class _FakeResult:
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
    from app.services.graphrag.graph_store import Neo4jGraphStore

    store = Neo4jGraphStore(driver, "neo4j", embedding_provider=MagicMock())
    await store.search_chunk_vector([0.1, 0.2], 5, tenant_id="tenant-a")
    await store.search_fact_vector([0.1, 0.2], 5, tenant_id="tenant-a")
    for cypher, _ in captured:
        assert "coalesce(v.visibility, 'PUBLIC') = 'PUBLIC'" in cypher
        assert "v.effectiveFrom" in cypher
        assert "v.effectiveTo" in cypher


def test_mixed_safe_and_profane_document_quarantines_only_unsafe_segment():
    wf = KnowledgeWorkflowService()
    admin = TrustedTenantContext(tenant_id="tenant-a", user_id="a1", roles=("ADMIN",))
    item = wf.create_draft(
        admin,
        source_type=KnowledgeSourceType.DOCUMENT,
        scope_key="training_knowledge",
        title="Tài liệu hỗn hợp",
        raw_text=(
            "Tập luyện Taekwondo giúp trẻ tăng cường thể lực và kỷ luật. "
            "Đoạn này có từ fuck không hợp lệ."
        ),
    )
    assert item.status == KnowledgeItemStatus.DRAFT
    assert len(item.accepted_facts) == 1
    assert "kỷ luật" in item.accepted_facts[0].statement
    assert len(item.quarantined_segments) >= 1
