"""Tenant-authenticated Knowledge Governance API (PostgreSQL + Neo4j GraphRAG)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.ingestion_runtime import get_service_container
from app.core.tenant_context import TrustedTenantContext, require_internal_tenant_context
from app.db.session import AsyncSessionLocal
from app.services.knowledge.document_extractor import extract_text_from_document
from app.services.knowledge.governance_pipeline import CandidateFact
from app.services.knowledge.knowledge_store import TenantKnowledgeStore
from app.services.knowledge.workflow_service import (
    KnowledgeItemRecord,
    KnowledgeItemStatus,
    KnowledgeSourceType,
    KnowledgeWorkflowService,
)

router = APIRouter(prefix="/api/v1/knowledge", tags=["Knowledge Governance"])


async def _publish_to_neo4j(record: KnowledgeItemRecord) -> dict[str, Any]:
    container = await get_service_container()
    store = TenantKnowledgeStore(
        container.neo4j_driver,
        settings.NEO4J_DATABASE,
        embedding_provider=container.embedding_provider,
    )
    return await store.publish_governance_item(
        tenant_id=record.tenant_id,
        item_id=record.item_id,
        title=record.title,
        scope_key=record.scope_key,
        facts=[f.model_dump(mode="json") for f in record.accepted_facts],
        visibility=record.visibility.value,
        effective_from=record.effective_from.isoformat() if record.effective_from else None,
        effective_to=record.effective_to.isoformat() if record.effective_to else None,
    )


_workflow_service = KnowledgeWorkflowService(
    session_factory=AsyncSessionLocal,
    graph_publisher=_publish_to_neo4j,
)


class RichTextKnowledgeRequest(BaseModel):
    scope_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    effective_from: date | None = None
    effective_to: date | None = None


class FaqKnowledgeRequest(BaseModel):
    scope_key: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    effective_from: date | None = None
    effective_to: date | None = None


class ReviewActionRequest(BaseModel):
    reason: str | None = None


class SchemaProposalCreateRequest(BaseModel):
    proposal_type: str = Field(min_length=1)
    technical_name: str = Field(min_length=1)
    scope_key: str = Field(min_length=1)
    entity_type: str | None = None
    reason: str = ""


_FORM_ENTITY_SCOPE: dict[str, tuple[str, str]] = {
    "center-profile": ("center_profile", "center_overview"),
    "facility-item": ("facility_item", "center_overview"),
    "policy-rule": ("policy_rule", "admission_onboarding"),
    "class-suitability": ("class_suitability_profile", "admission_onboarding"),
    "enrollment-step": ("enrollment_step", "admission_onboarding"),
    "taekwondo-concept": ("taekwondo_concept", "training_knowledge"),
    "poomsae": ("poomsae", "belt_and_exam"),
    "belt-rank": ("belt_rank", "belt_and_exam"),
    "discount-rule": ("discount_rule", "tuition_and_discount"),
}

_FORM_PRIMARY_PREDICATE: dict[str, str] = {
    "center-profile": "history",
    "facility-item": "description",
    "policy-rule": "summary",
    "class-suitability": "curriculum_summary",
    "enrollment-step": "description",
    "taekwondo-concept": "description",
    "poomsae": "description",
    "belt-rank": "exam_requirements",
    "discount-rule": "discount_value",
}


_FORM_AUTO_CODE_FIELD: dict[str, tuple[str, str]] = {
    "policy-rule": ("policy_code", "policy"),
    "class-suitability": ("profile_code", "profile"),
    "enrollment-step": ("step_code", "step"),
    "discount-rule": ("rule_code", "rule"),
}


def _prepare_form_submission(
    form_type: str,
    payload: dict[str, Any],
    ctx: TrustedTenantContext,
    *,
    existing_payload: dict[str, Any] | None = None,
    item_id: str | None = None,
) -> tuple[str, str, str, list[CandidateFact], dict[str, Any]]:
    if form_type not in _FORM_ENTITY_SCOPE:
        raise HTTPException(status_code=404, detail=f"Unknown knowledge form type: {form_type}")
    entity_type, default_scope = _FORM_ENTITY_SCOPE[form_type]
    sanitized_payload = {**payload, "tenant_id": ctx.tenant_id}
    if form_type == "center-profile":
        # A center has exactly one profile per tenant; legacy clients must not
        # persist the retired user-managed profile code.
        sanitized_payload.pop("profile_code", None)
    if form_type in _FORM_AUTO_CODE_FIELD:
        code_field, prefix = _FORM_AUTO_CODE_FIELD[form_type]
        if existing_payload is not None:
            existing_code = str(existing_payload.get(code_field) or "").strip()
            if existing_code:
                sanitized_payload[code_field] = existing_code
            elif not str(sanitized_payload.get(code_field) or "").strip():
                sanitized_payload[code_field] = f"{prefix}-{item_id or uuid4()}"
        elif not str(sanitized_payload.get(code_field) or "").strip():
            sanitized_payload[code_field] = f"{prefix}-{uuid4()}"
    title = str(
        sanitized_payload.get("title")
        or sanitized_payload.get("name")
        or sanitized_payload.get("policy_code")
        or sanitized_payload.get("step_code")
        or sanitized_payload.get("rule_code")
        or form_type
    )
    summary_text = str(
        sanitized_payload.get("summary")
        or sanitized_payload.get("description")
        or sanitized_payload.get("history")
        or title
    )
    primary_predicate = _FORM_PRIMARY_PREDICATE.get(form_type, "summary")
    facts = [
        CandidateFact(
            fact_id=f"{ctx.tenant_id}:{form_type}:{title}",
            entity_type=entity_type,
            property_name=primary_predicate,
            predicate=primary_predicate,
            fact_type="PROPERTY",
            subject_key=f"{ctx.tenant_id}:{entity_type}:{title}",
            source_type="FORM",
            form_type=form_type,
            statement=f"{title}: {summary_text}",
            scope_key=default_scope,
        )
    ]
    return default_scope, title, summary_text, facts, sanitized_payload


@router.post("/forms/{form_type}", status_code=status.HTTP_202_ACCEPTED)
async def submit_structured_form(
    form_type: str,
    payload: dict[str, Any],
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
) -> KnowledgeItemRecord:
    default_scope, title, summary_text, facts, sanitized_payload = _prepare_form_submission(
        form_type, payload, ctx
    )
    return await _workflow_service.acreate_processing_form(
        ctx,
        scope_key=default_scope,
        title=title,
        raw_text=summary_text,
        candidate_facts=facts,
        form_type=form_type,
        structured_payload=sanitized_payload,
    )


@router.put("/items/{item_id}/form")
async def update_structured_form_item(
    item_id: str,
    payload: dict[str, Any],
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
) -> KnowledgeItemRecord:
    try:
        existing = await _workflow_service.aget_item(ctx, item_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not existing.form_type or existing.form_type not in _FORM_ENTITY_SCOPE:
        raise HTTPException(status_code=400, detail="Knowledge item is not a valid structured form.")
    default_scope, title, summary_text, facts, sanitized_payload = _prepare_form_submission(
        existing.form_type,
        payload,
        ctx,
        existing_payload=existing.structured_payload,
        item_id=item_id,
    )
    try:
        return await _workflow_service.aupdate_form_item(
            ctx,
            item_id,
            scope_key=default_scope,
            title=title,
            raw_text=summary_text,
            candidate_facts=facts,
            form_type=existing.form_type,
            structured_payload=sanitized_payload,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/items/{item_id}/retry")
async def retry_structured_form_item(
    item_id: str,
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
) -> KnowledgeItemRecord:
    try:
        return await _workflow_service.aretry_form_item(ctx, item_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def get_knowledge_workflow_service() -> KnowledgeWorkflowService:
    return _workflow_service


@router.get("/forms/{form_type}/published")
async def get_latest_published_form(
    form_type: str,
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
) -> dict[str, Any]:
    if form_type not in _FORM_ENTITY_SCOPE:
        raise HTTPException(status_code=404, detail=f"Unknown knowledge form type: {form_type}")
    items = await _workflow_service.alist_items(ctx, status=KnowledgeItemStatus.PUBLISHED)
    matching = [
        item
        for item in items
        if item.form_type == form_type and item.tenant_id == ctx.tenant_id
    ]
    if not matching:
        raise HTTPException(status_code=404, detail=f"No published form found for {form_type}")
    latest = max(matching, key=lambda item: item.updated_at)
    return {
        "item_id": latest.item_id,
        "form_type": latest.form_type,
        "tenant_id": latest.tenant_id,
        "status": latest.status.value,
        "structured_payload": latest.structured_payload or {},
        "updated_at": latest.updated_at.isoformat(),
        "published_at": latest.updated_at.isoformat(),
    }


@router.post("/rich-text")
async def submit_rich_text(
    body: RichTextKnowledgeRequest,
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
) -> KnowledgeItemRecord:
    return await _workflow_service.acreate_draft(
        ctx,
        source_type=KnowledgeSourceType.RICH_TEXT,
        scope_key=body.scope_key,
        title=body.title,
        raw_text=body.content,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
    )


@router.post("/documents")
async def upload_knowledge_document(
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
    scope_key: Annotated[str, Form()],
    title: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
) -> KnowledgeItemRecord:
    if file is None:
        raise HTTPException(status_code=400, detail="Document file is required.")
    raw_bytes = await file.read()
    try:
        text = extract_text_from_document(file.filename or "", raw_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _workflow_service.acreate_draft(
        ctx,
        source_type=KnowledgeSourceType.DOCUMENT,
        scope_key=scope_key,
        title=title or (file.filename or "Document"),
        raw_text=text,
    )


@router.post("/faq")
async def submit_faq(
    body: FaqKnowledgeRequest,
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
) -> KnowledgeItemRecord:
    combined = f"Hỏi: {body.question.strip()}\nĐáp: {body.answer.strip()}"
    return await _workflow_service.acreate_draft(
        ctx,
        source_type=KnowledgeSourceType.FAQ,
        scope_key=body.scope_key,
        title=body.question.strip(),
        raw_text=combined,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
    )


@router.get("/items")
async def list_knowledge_items(
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
    item_status: Annotated[KnowledgeItemStatus | None, Query(alias="status")] = None,
) -> list[KnowledgeItemRecord]:
    return await _workflow_service.alist_items(ctx, status=item_status)


@router.get("/items/{item_id}")
async def get_knowledge_item(
    item_id: str,
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
) -> KnowledgeItemRecord:
    try:
        return await _workflow_service.aget_item(ctx, item_id)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/items/{item_id}/submit")
async def submit_for_review(
    item_id: str,
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
) -> KnowledgeItemRecord:
    return await _transition_or_400(ctx, item_id, KnowledgeItemStatus.PENDING_REVIEW)


@router.post("/items/{item_id}/reject")
async def reject_item(
    item_id: str,
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
    body: ReviewActionRequest | None = None,
) -> KnowledgeItemRecord:
    return await _transition_or_400(
        ctx, item_id, KnowledgeItemStatus.REJECTED, reason=body.reason if body else None
    )


@router.post("/items/{item_id}/publish")
async def publish_item(
    item_id: str,
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
) -> KnowledgeItemRecord:
    return await _transition_or_400(ctx, item_id, KnowledgeItemStatus.PUBLISHED)


@router.delete("/items/{item_id}")
async def delete_item(
    item_id: str,
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
) -> dict[str, Any]:
    from sqlalchemy import delete as sa_delete, select as sa_select
    from app.models.knowledge_governance import KnowledgeGovernanceItem, KnowledgePublishJob

    upper_roles = {r.strip().upper() for r in ctx.roles if r.strip()}
    if not (upper_roles & {"SYSTEM_ADMIN", "ADMIN", "OWNER"}) and not ctx.has_permission(
        "KNOWLEDGE_PUBLISH"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing KNOWLEDGE_PUBLISH permission to delete knowledge item.",
        )

    async with AsyncSessionLocal() as session, session.begin():
        row = await session.scalar(
            sa_select(KnowledgeGovernanceItem).where(
                KnowledgeGovernanceItem.item_id == item_id
            )
        )
        if row is None or row.tenant_id != ctx.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Knowledge item not found in current tenant.",
            )
        await session.execute(
            sa_delete(KnowledgePublishJob).where(
                KnowledgePublishJob.item_id == item_id,
                KnowledgePublishJob.tenant_id == ctx.tenant_id,
            )
        )
        await session.execute(
            sa_delete(KnowledgeGovernanceItem).where(
                KnowledgeGovernanceItem.item_id == item_id,
                KnowledgeGovernanceItem.tenant_id == ctx.tenant_id,
            )
        )

    container = await get_service_container()
    store = TenantKnowledgeStore(container.neo4j_driver, settings.NEO4J_DATABASE)
    deleted_neo4j = await store.delete_governance_item(
        tenant_id=ctx.tenant_id, item_id=item_id
    )
    return {"deleted": True, "item_id": item_id, "neo4j_deleted": deleted_neo4j}


@router.post("/proposals")
async def create_schema_proposal(
    body: SchemaProposalCreateRequest,
    ctx: Annotated[TrustedTenantContext, Depends(require_internal_tenant_context)],
):
    try:
        return await _workflow_service.acreate_schema_proposal(
            ctx,
            proposal_type=body.proposal_type,
            technical_name=body.technical_name,
            scope_key=body.scope_key,
            entity_type=body.entity_type,
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _transition_or_400(
    ctx: TrustedTenantContext,
    item_id: str,
    target: KnowledgeItemStatus,
    *,
    reason: str | None = None,
) -> KnowledgeItemRecord:
    try:
        return await _workflow_service.atransition(
            ctx, item_id, target, rejection_reason=reason
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
