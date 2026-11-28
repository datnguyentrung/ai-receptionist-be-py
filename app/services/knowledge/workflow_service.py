"""Step 4 & 5: Persistent Multi-Tenant Knowledge Workflow Service (PostgreSQL + Neo4j GraphRAG)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.tenant_context import TrustedTenantContext
from app.core.config import settings
from app.models.knowledge_governance import KnowledgeGovernanceItem, KnowledgePublishJob, KnowledgeTenantProposal
from app.services.knowledge.document_extractor import extract_candidate_facts
from app.services.knowledge.governance_pipeline import (
    CandidateFact,
    ConflictRecord,
    GateSeverity,
    KnowledgeGovernancePipeline,
    RejectedFact,
    ValidatorOutcome,
)
from app.services.ontology.capability_registry import CapabilityRegistry
from app.services.ontology.ownership_registry import OwnershipRegistry, VisibilityLevel


class KnowledgeSourceType(StrEnum):
    FORM = "FORM"
    RICH_TEXT = "RICH_TEXT"
    DOCUMENT = "DOCUMENT"
    FAQ = "FAQ"


class KnowledgeItemStatus(StrEnum):
    DRAFT = "DRAFT"
    PROCESSING = "PROCESSING"
    PENDING_REVIEW = "PENDING_REVIEW"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"


_ALLOWED_TRANSITIONS: dict[KnowledgeItemStatus, set[KnowledgeItemStatus]] = {
    KnowledgeItemStatus.DRAFT: {
        KnowledgeItemStatus.PROCESSING,
        KnowledgeItemStatus.PENDING_REVIEW,
        KnowledgeItemStatus.REJECTED,
        KnowledgeItemStatus.QUARANTINED,
    },
    KnowledgeItemStatus.PENDING_REVIEW: {
        KnowledgeItemStatus.PUBLISHED,
        KnowledgeItemStatus.REJECTED,
        KnowledgeItemStatus.QUARANTINED,
        KnowledgeItemStatus.DRAFT,
    },
    KnowledgeItemStatus.PROCESSING: {
        KnowledgeItemStatus.PUBLISHED,
        KnowledgeItemStatus.DRAFT,
    },
    KnowledgeItemStatus.PUBLISHED: set(),
    KnowledgeItemStatus.REJECTED: {
        KnowledgeItemStatus.DRAFT,
    },
    KnowledgeItemStatus.QUARANTINED: set(),
}


class KnowledgeItemRecord(BaseModel):
    item_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    source_type: KnowledgeSourceType
    scope_key: str
    title: str
    raw_text: str
    version: int = 1
    parent_item_id: str | None = None
    status: KnowledgeItemStatus = KnowledgeItemStatus.DRAFT
    visibility: VisibilityLevel = VisibilityLevel.PUBLIC
    author_user_id: str
    reviewer_user_id: str | None = None
    rejection_reason: str | None = None
    form_type: str | None = None
    structured_payload: dict[str, Any] | None = None
    evidence: list[str] = Field(default_factory=list)
    validator_outcomes: list[ValidatorOutcome] = Field(default_factory=list)
    accepted_facts: list[CandidateFact] = Field(default_factory=list)
    rejected_facts: list[RejectedFact] = Field(default_factory=list)
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    quarantined_segments: list[str] = Field(default_factory=list)
    effective_from: date | None = None
    effective_to: date | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    processing_started_at: datetime | None = None
    published_at: datetime | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None


class TenantSchemaProposal(BaseModel):
    proposal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str
    proposal_type: str
    technical_name: str
    entity_type: str | None = None
    scope_key: str
    reason: str = ""
    status: str = "PROPOSED"
    author_user_id: str
    reviewed_by: str | None = None
    rejection_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


GraphPublishFn = Callable[[KnowledgeItemRecord], Awaitable[dict[str, Any]]]
GraphArchiveFn = Callable[[str, str], Awaitable[bool]]


class KnowledgeWorkflowService:
    """Persistent multi-tenant knowledge workflow backed by PostgreSQL + Neo4j GraphRAG."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        graph_publisher: GraphPublishFn | None = None,
        graph_archiver: GraphArchiveFn | None = None,
        pipeline: KnowledgeGovernancePipeline | None = None,
        ownership_registry: OwnershipRegistry | None = None,
        capability_registry: CapabilityRegistry | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._graph_publisher = graph_publisher
        self._graph_archiver = graph_archiver
        self._ownership = ownership_registry or OwnershipRegistry.from_seeds()
        self._caps = capability_registry or CapabilityRegistry.default()
        self._pipeline = pipeline or KnowledgeGovernancePipeline(
            ownership_registry=self._ownership,
            capability_registry=self._caps,
        )
        # In-memory store used only when session_factory is None (isolated unit tests)
        self._items: dict[str, KnowledgeItemRecord] = {}
        self._proposals: dict[str, TenantSchemaProposal] = {}

    def _require_permission(
        self, ctx: TrustedTenantContext, target_status: KnowledgeItemStatus
    ) -> None:
        """Default-deny RBAC: require explicit permission or ADMIN/OWNER role."""
        upper_roles = {r.strip().upper() for r in ctx.roles if r.strip()}
        if upper_roles & {"SYSTEM_ADMIN", "ADMIN", "OWNER"}:
            return
        if target_status == KnowledgeItemStatus.REJECTED:
            if ctx.has_permission("KNOWLEDGE_REVIEW"):
                return
        if target_status == KnowledgeItemStatus.PUBLISHED:
            if ctx.has_permission("KNOWLEDGE_PUBLISH"):
                return
        raise PermissionError(
            f"Account lacks permission for transition to {target_status.value}."
        )

    def _build_draft_record(
        self,
        ctx: TrustedTenantContext,
        *,
        source_type: KnowledgeSourceType,
        scope_key: str,
        title: str,
        raw_text: str,
        published_facts: list[dict[str, Any]],
        candidate_facts: list[CandidateFact] | None = None,
        form_type: str | None = None,
        structured_payload: dict[str, Any] | None = None,
        effective_from: date | None = None,
        effective_to: date | None = None,
        visibility: VisibilityLevel = VisibilityLevel.PUBLIC,
        skip_scope_keyword_check: bool = False,
    ) -> KnowledgeItemRecord:
        if not ctx.tenant_id or not ctx.tenant_id.strip():
            raise ValueError("tenant_id is required")

        facts = candidate_facts
        if facts is None:
            facts = extract_candidate_facts(
                tenant_id=ctx.tenant_id, scope_key=scope_key, raw_text=raw_text
            )

        evaluation = self._pipeline.evaluate(
            tenant_id=ctx.tenant_id,
            scope_key=scope_key,
            raw_text=raw_text,
            candidate_facts=facts,
            published_facts=published_facts,
            skip_scope_keyword_check=skip_scope_keyword_check,
        )

        if evaluation.overall_severity == GateSeverity.QUARANTINE:
            initial_status = KnowledgeItemStatus.QUARANTINED
        elif evaluation.overall_severity == GateSeverity.REJECT:
            initial_status = KnowledgeItemStatus.REJECTED
        else:
            initial_status = KnowledgeItemStatus.DRAFT

        return KnowledgeItemRecord(
            tenant_id=ctx.tenant_id,
            source_type=source_type,
            scope_key=scope_key,
            title=title.strip() or "Untitled",
            raw_text=raw_text,
            status=initial_status,
            visibility=visibility,
            author_user_id=ctx.user_id,
            form_type=form_type,
            structured_payload=structured_payload,
            evidence=[raw_text[:300]] if raw_text else [],
            validator_outcomes=evaluation.validator_outcomes,
            accepted_facts=evaluation.accepted_facts,
            rejected_facts=evaluation.rejected_facts,
            conflicts=evaluation.conflicts,
            quarantined_segments=evaluation.quarantined_segments,
            effective_from=effective_from,
            effective_to=effective_to,
        )

    def _validate_transition(
        self,
        ctx: TrustedTenantContext,
        item: KnowledgeItemRecord,
        target_status: KnowledgeItemStatus,
        published_facts: list[dict[str, Any]],
    ) -> None:
        if target_status not in _ALLOWED_TRANSITIONS.get(item.status, set()):
            raise ValueError(
                f"Invalid state transition: {item.status.value} -> {target_status.value}"
            )
        if target_status in {
            KnowledgeItemStatus.PUBLISHED,
            KnowledgeItemStatus.REJECTED,
        }:
            self._require_permission(ctx, target_status)

        if target_status == KnowledgeItemStatus.PUBLISHED:
            gate = self._pipeline.evaluate(
                tenant_id=ctx.tenant_id,
                scope_key=item.scope_key,
                raw_text=item.raw_text,
                candidate_facts=item.accepted_facts,
                published_facts=published_facts,
                skip_scope_keyword_check=(item.source_type == KnowledgeSourceType.FORM),
            )
            if (
                gate.overall_severity in {GateSeverity.REJECT, GateSeverity.QUARANTINE}
                or not gate.accepted_facts
                or gate.conflicts
                or gate.needs_entity_review
            ):
                raise ValueError(
                    "Publication Gate blocked publish: item has rejected ownership, safety, scope, or unresolved conflict issues."
                )

    # ------------------------------------------------------------------
    # Async PostgreSQL + Neo4j API (production runtime path)
    # ------------------------------------------------------------------

    async def _db_published_facts(
        self, session: AsyncSession, tenant_id: str
    ) -> list[dict[str, Any]]:
        today = datetime.now(UTC).date()
        rows = (
            await session.scalars(
                select(KnowledgeGovernanceItem).where(
                    KnowledgeGovernanceItem.tenant_id == tenant_id,
                    KnowledgeGovernanceItem.status == KnowledgeItemStatus.PUBLISHED.value,
                )
            )
        ).all()
        facts: list[dict[str, Any]] = []
        for row in rows:
            if row.effective_to and row.effective_to < today:
                continue
            for idx, f in enumerate(row.accepted_facts or []):
                facts.append(
                    {
                        "fact_id": f.get("fact_id") or f"{row.item_id}:{idx}",
                        "entity_type": f.get("entity_type", "policy_rule"),
                        "scope_key": f.get("scope_key", row.scope_key),
                        "statement": f.get("statement", ""),
                    }
                )
        return facts

    async def acreate_draft(
        self,
        ctx: TrustedTenantContext,
        *,
        source_type: KnowledgeSourceType,
        scope_key: str,
        title: str,
        raw_text: str,
        candidate_facts: list[CandidateFact] | None = None,
        form_type: str | None = None,
        structured_payload: dict[str, Any] | None = None,
        effective_from: date | None = None,
        effective_to: date | None = None,
        visibility: VisibilityLevel = VisibilityLevel.PUBLIC,
        skip_scope_keyword_check: bool = False,
    ) -> KnowledgeItemRecord:
        if self._session_factory is None:
            return self.create_draft(
                ctx,
                source_type=source_type,
                scope_key=scope_key,
                title=title,
                raw_text=raw_text,
                candidate_facts=candidate_facts,
                form_type=form_type,
                structured_payload=structured_payload,
                effective_from=effective_from,
                effective_to=effective_to,
                visibility=visibility,
                skip_scope_keyword_check=skip_scope_keyword_check,
            )

        async with self._session_factory() as session, session.begin():
            published = await self._db_published_facts(session, ctx.tenant_id)
            record = self._build_draft_record(
                ctx,
                source_type=source_type,
                scope_key=scope_key,
                title=title,
                raw_text=raw_text,
                published_facts=published,
                candidate_facts=candidate_facts,
                form_type=form_type,
                structured_payload=structured_payload,
                effective_from=effective_from,
                effective_to=effective_to,
                visibility=visibility,
                skip_scope_keyword_check=skip_scope_keyword_check,
            )
            session.add(_to_orm_item(record))
        return record

    async def acreate_processing_form(
        self,
        ctx: TrustedTenantContext,
        *,
        scope_key: str,
        title: str,
        raw_text: str,
        candidate_facts: list[CandidateFact],
        form_type: str,
        structured_payload: dict[str, Any],
    ) -> KnowledgeItemRecord:
        """Persist a form and its durable outbox message in one transaction."""
        if self._session_factory is None:
            record = self.create_draft(
                ctx, source_type=KnowledgeSourceType.FORM, scope_key=scope_key,
                title=title, raw_text=raw_text, candidate_facts=candidate_facts,
                form_type=form_type, structured_payload=structured_payload,
                skip_scope_keyword_check=True,
            )
            if record.status == KnowledgeItemStatus.DRAFT:
                record.status = KnowledgeItemStatus.PROCESSING
            return record

        async with self._session_factory() as session, session.begin():
            published = await self._db_published_facts(session, ctx.tenant_id)
            record = self._build_draft_record(
                ctx, source_type=KnowledgeSourceType.FORM, scope_key=scope_key,
                title=title, raw_text=raw_text, published_facts=published,
                candidate_facts=candidate_facts, form_type=form_type,
                structured_payload=structured_payload, skip_scope_keyword_check=True,
            )
            # Unsafe content remains a draft for correction; successful form
            # submissions progress asynchronously to Neo4j.
            if record.status == KnowledgeItemStatus.DRAFT:
                record.status = KnowledgeItemStatus.PROCESSING
                record.processing_started_at = datetime.now(UTC)
            session.add(_to_orm_item(record))
            if record.status == KnowledgeItemStatus.PROCESSING:
                session.add(KnowledgePublishJob(
                    job_id=str(uuid.uuid4()), item_id=record.item_id,
                    tenant_id=record.tenant_id, status="PENDING",
                ))
        return record

    async def apublish_processing_form(
        self, item_id: str, target_version: int | None = None
    ) -> None:
        """Worker-only, idempotent publication of a queued standard form."""
        if self._session_factory is None:
            return
        async with self._session_factory() as session:
            row = await session.scalar(select(KnowledgeGovernanceItem).where(
                KnowledgeGovernanceItem.item_id == item_id
            ))
            if row is None or row.status != KnowledgeItemStatus.PROCESSING.value:
                return
            if target_version is not None and row.version != target_version:
                return
            record = _from_orm_item(row)
        try:
            if self._graph_publisher is None:
                raise RuntimeError("Neo4j publisher is not configured")
            await self._graph_publisher(record)
        except Exception as exc:
            await self._fail_processing_form(item_id, exc, target_version=target_version)
            raise
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(select(KnowledgeGovernanceItem).where(
                KnowledgeGovernanceItem.item_id == item_id
            ).with_for_update())
            if row is None or row.status != KnowledgeItemStatus.PROCESSING.value:
                return
            if target_version is not None and row.version != target_version:
                return
            row.status = KnowledgeItemStatus.PUBLISHED.value
            row.updated_at = datetime.now(UTC)
            row.published_at = row.updated_at
            payload = dict(row.structured_payload or {})
            payload["_graph_sync"] = "SYNCED"
            row.structured_payload = payload
            job = await session.scalar(select(KnowledgePublishJob).where(KnowledgePublishJob.item_id == item_id))
            if job is not None:
                job.status = "PUBLISHED"
                job.last_error_code = None
                job.last_error_message = None

    async def _fail_processing_form(
        self, item_id: str, exc: Exception, *, target_version: int | None = None
    ) -> None:
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(select(KnowledgeGovernanceItem).where(
                KnowledgeGovernanceItem.item_id == item_id
            ).with_for_update())
            job = await session.scalar(select(KnowledgePublishJob).where(KnowledgePublishJob.item_id == item_id))
            if row is None or job is None:
                return
            if target_version is not None and row.version != target_version:
                return
            job.attempt_count += 1
            job.last_error_code = type(exc).__name__
            job.last_error_message = str(exc)[:1000]
            if job.attempt_count >= settings.KNOWLEDGE_PUBLISH_MAX_ATTEMPTS:
                job.status = "FAILED"
                row.status = KnowledgeItemStatus.DRAFT.value
                row.last_error_code = job.last_error_code
                row.last_error_message = job.last_error_message
                row.rejection_reason = "Không thể công bố lên Neo4j. Vui lòng thử lại."
            else:
                job.status = "PENDING"
            row.updated_at = datetime.now(UTC)

    async def _upsert_publish_job(
        self,
        session: AsyncSession,
        *,
        item_id: str,
        tenant_id: str,
        target_version: int = 1,
    ) -> None:
        job = await session.scalar(
            select(KnowledgePublishJob).where(KnowledgePublishJob.item_id == item_id)
        )
        now = datetime.now(UTC)
        if job is None:
            session.add(
                KnowledgePublishJob(
                    job_id=str(uuid.uuid4()),
                    item_id=item_id,
                    tenant_id=tenant_id,
                    status="PENDING",
                    target_version=target_version,
                    attempt_count=0,
                )
            )
            return
        job.status = "PENDING"
        job.target_version = target_version
        job.attempt_count = 0
        job.last_error_code = None
        job.last_error_message = None
        job.updated_at = now

    async def aupdate_form_item(
        self,
        ctx: TrustedTenantContext,
        item_id: str,
        *,
        scope_key: str,
        title: str,
        raw_text: str,
        candidate_facts: list[CandidateFact],
        form_type: str,
        structured_payload: dict[str, Any],
    ) -> KnowledgeItemRecord:
        """Update an existing DRAFT or REJECTED FORM item in-place, bump version, and re-queue publish."""
        if self._session_factory is None:
            existing = self.get_item(ctx, item_id)
            if existing.source_type != KnowledgeSourceType.FORM:
                raise ValueError("Only FORM items can be updated via the form endpoint.")
            rebuilt = self._build_draft_record(
                ctx,
                source_type=KnowledgeSourceType.FORM,
                scope_key=scope_key,
                title=title,
                raw_text=raw_text,
                published_facts=[],
                candidate_facts=candidate_facts,
                form_type=form_type,
                structured_payload=structured_payload,
                skip_scope_keyword_check=True,
            )
            updated = existing.model_copy(
                update={
                    "scope_key": rebuilt.scope_key,
                    "title": rebuilt.title,
                    "raw_text": rebuilt.raw_text,
                    "version": existing.version + 1,
                    "form_type": form_type,
                    "structured_payload": structured_payload,
                    "validator_outcomes": rebuilt.validator_outcomes,
                    "accepted_facts": rebuilt.accepted_facts,
                    "rejected_facts": rebuilt.rejected_facts,
                    "conflicts": rebuilt.conflicts,
                    "quarantined_segments": rebuilt.quarantined_segments,
                    "rejection_reason": None,
                    "last_error_code": None,
                    "last_error_message": None,
                    "status": (
                        KnowledgeItemStatus.PROCESSING
                        if rebuilt.status == KnowledgeItemStatus.DRAFT
                        else rebuilt.status
                    ),
                    "processing_started_at": (
                        datetime.now(UTC)
                        if rebuilt.status == KnowledgeItemStatus.DRAFT
                        else None
                    ),
                    "updated_at": datetime.now(UTC),
                }
            )
            self._items[item_id] = updated
            return updated

        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(KnowledgeGovernanceItem).where(
                    KnowledgeGovernanceItem.item_id == item_id,
                    KnowledgeGovernanceItem.tenant_id == ctx.tenant_id,
                ).with_for_update()
            )
            if row is None:
                raise PermissionError("Knowledge item not found in current tenant.")
            if row.source_type != KnowledgeSourceType.FORM.value:
                raise ValueError("Only FORM items can be updated via the form endpoint.")

            published = await self._db_published_facts(session, ctx.tenant_id)
            rebuilt = self._build_draft_record(
                ctx,
                source_type=KnowledgeSourceType.FORM,
                scope_key=scope_key,
                title=title,
                raw_text=raw_text,
                published_facts=published,
                candidate_facts=candidate_facts,
                form_type=form_type,
                structured_payload=structured_payload,
                skip_scope_keyword_check=True,
            )
            now = datetime.now(UTC)
            row.scope_key = rebuilt.scope_key
            row.title = rebuilt.title
            row.raw_text = rebuilt.raw_text
            row.version = (row.version or 1) + 1
            row.form_type = form_type
            row.structured_payload = structured_payload
            row.validator_outcomes = [v.model_dump(mode="json") for v in rebuilt.validator_outcomes]
            row.accepted_facts = [f.model_dump(mode="json") for f in rebuilt.accepted_facts]
            row.rejected_facts = [r.model_dump(mode="json") for r in rebuilt.rejected_facts]
            row.conflicts = [c.model_dump(mode="json") for c in rebuilt.conflicts]
            row.quarantined_segments = rebuilt.quarantined_segments
            row.rejection_reason = None
            row.last_error_code = None
            row.last_error_message = None
            row.updated_at = now

            if rebuilt.status == KnowledgeItemStatus.DRAFT:
                row.status = KnowledgeItemStatus.PROCESSING.value
                row.processing_started_at = now
                await self._upsert_publish_job(
                    session, item_id=row.item_id, tenant_id=ctx.tenant_id, target_version=row.version
                )
            else:
                row.status = rebuilt.status.value
                row.processing_started_at = None
            await session.flush()
            return _from_orm_item(row)

    async def aretry_form_item(
        self, ctx: TrustedTenantContext, item_id: str
    ) -> KnowledgeItemRecord:
        """Retry publishing a DRAFT FORM item whose publish job failed, preserving version & payload."""
        if self._session_factory is None:
            existing = self.get_item(ctx, item_id)
            if existing.source_type != KnowledgeSourceType.FORM:
                raise ValueError("Only FORM items can be retried.")
            if existing.status != KnowledgeItemStatus.DRAFT or not (
                existing.last_error_code or existing.last_error_message
            ):
                raise ValueError("Only DRAFT form items with a failed publish job can be retried.")
            updated = existing.model_copy(
                update={
                    "status": KnowledgeItemStatus.PROCESSING,
                    "rejection_reason": None,
                    "last_error_code": None,
                    "last_error_message": None,
                    "processing_started_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            )
            self._items[item_id] = updated
            return updated

        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(KnowledgeGovernanceItem).where(
                    KnowledgeGovernanceItem.item_id == item_id,
                    KnowledgeGovernanceItem.tenant_id == ctx.tenant_id,
                ).with_for_update()
            )
            if row is None:
                raise PermissionError("Knowledge item not found in current tenant.")
            if row.source_type != KnowledgeSourceType.FORM.value:
                raise ValueError("Only FORM items can be retried.")
            job = await session.scalar(
                select(KnowledgePublishJob).where(
                    KnowledgePublishJob.item_id == item_id,
                    KnowledgePublishJob.tenant_id == ctx.tenant_id,
                )
            )
            has_failed_job = job is not None and job.status == "FAILED"
            has_publish_error = bool(row.last_error_code or row.last_error_message)
            if row.status != KnowledgeItemStatus.DRAFT.value or not (has_failed_job or has_publish_error):
                raise ValueError("Only DRAFT form items with a failed publish job can be retried.")

            now = datetime.now(UTC)
            row.status = KnowledgeItemStatus.PROCESSING.value
            row.rejection_reason = None
            row.last_error_code = None
            row.last_error_message = None
            row.processing_started_at = now
            row.updated_at = now
            await self._upsert_publish_job(
                session, item_id=row.item_id, tenant_id=ctx.tenant_id
            )
            await session.flush()
            return _from_orm_item(row)

    async def alist_items(
        self, ctx: TrustedTenantContext, *, status: KnowledgeItemStatus | None = None
    ) -> list[KnowledgeItemRecord]:
        if self._session_factory is None:
            return self.list_items(ctx, status=status)
        async with self._session_factory() as session:
            stmt = select(KnowledgeGovernanceItem).where(
                KnowledgeGovernanceItem.tenant_id == ctx.tenant_id
            )
            if status is not None:
                stmt = stmt.where(KnowledgeGovernanceItem.status == status.value)
            stmt = stmt.order_by(KnowledgeGovernanceItem.updated_at.desc())
            rows = (await session.scalars(stmt)).all()
            return [_from_orm_item(row) for row in rows]

    async def aget_item(self, ctx: TrustedTenantContext, item_id: str) -> KnowledgeItemRecord:
        if self._session_factory is None:
            return self.get_item(ctx, item_id)
        async with self._session_factory() as session:
            row = await session.scalar(
                select(KnowledgeGovernanceItem).where(
                    KnowledgeGovernanceItem.item_id == item_id,
                    KnowledgeGovernanceItem.tenant_id == ctx.tenant_id,
                )
            )
            if row is None:
                raise PermissionError("Knowledge item not found in current tenant.")
            return _from_orm_item(row)

    async def atransition(
        self,
        ctx: TrustedTenantContext,
        item_id: str,
        target_status: KnowledgeItemStatus,
        *,
        rejection_reason: str | None = None,
    ) -> KnowledgeItemRecord:
        if self._session_factory is None:
            record = self.transition(
                ctx, item_id, target_status, rejection_reason=rejection_reason
            )
            if target_status == KnowledgeItemStatus.PUBLISHED and self._graph_publisher:
                await self._graph_publisher(record)
            return record

        previous_status: str | None = None
        updated_record: KnowledgeItemRecord | None = None
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(KnowledgeGovernanceItem).where(
                    KnowledgeGovernanceItem.item_id == item_id,
                    KnowledgeGovernanceItem.tenant_id == ctx.tenant_id,
                ).with_for_update()
            )
            if row is None:
                raise PermissionError("Knowledge item not found in current tenant.")
            record = _from_orm_item(row)
            published = (
                await self._db_published_facts(session, ctx.tenant_id)
                if target_status == KnowledgeItemStatus.PUBLISHED
                else []
            )
            self._validate_transition(ctx, record, target_status, published)

            previous_status = row.status
            row.status = target_status.value
            row.reviewer_user_id = ctx.user_id
            if rejection_reason:
                row.rejection_reason = rejection_reason
            if target_status == KnowledgeItemStatus.PUBLISHED:
                payload = dict(row.structured_payload or {})
                payload["_graph_sync"] = "PENDING_PUBLISH"
                row.structured_payload = payload
            row.updated_at = datetime.now(UTC)
            await session.flush()
            updated_record = _from_orm_item(row)

        # Commit PostgreSQL first, then sync Neo4j and mark _graph_sync = SYNCED.
        # If process crashes mid-way, reconcile_pending_graph_sync() retries idempotently.
        if target_status == KnowledgeItemStatus.PUBLISHED and self._graph_publisher:
            await self._graph_publisher(updated_record)
            await self._mark_graph_synced(ctx.tenant_id, updated_record.item_id)

        return updated_record

    async def _mark_graph_synced(self, tenant_id: str, item_id: str) -> None:
        if self._session_factory is None:
            return
        async with self._session_factory() as session, session.begin():
            row = await session.scalar(
                select(KnowledgeGovernanceItem).where(
                    KnowledgeGovernanceItem.item_id == item_id,
                    KnowledgeGovernanceItem.tenant_id == tenant_id,
                )
            )
            if row is not None:
                payload = dict(row.structured_payload or {})
                payload["_graph_sync"] = "SYNCED"
                row.structured_payload = payload

    async def reconcile_pending_graph_sync(self, tenant_id: str | None = None) -> int:
        """Retry any PostgreSQL-committed PUBLISHED items whose Neo4j sync is pending."""
        if self._session_factory is None:
            return 0
        synced = 0
        async with self._session_factory() as session:
            stmt = select(KnowledgeGovernanceItem).where(
                KnowledgeGovernanceItem.status == KnowledgeItemStatus.PUBLISHED.value
            )
            if tenant_id:
                stmt = stmt.where(KnowledgeGovernanceItem.tenant_id == tenant_id)
            rows = (await session.scalars(stmt)).all()
            pending = [
                _from_orm_item(r)
                for r in rows
                if (r.structured_payload or {}).get("_graph_sync") == "PENDING_PUBLISH"
            ]
        for rec in pending:
            if self._graph_publisher:
                await self._graph_publisher(rec)
                await self._mark_graph_synced(rec.tenant_id, rec.item_id)
                synced += 1
        return synced

    async def acreate_schema_proposal(
        self,
        ctx: TrustedTenantContext,
        *,
        proposal_type: str,
        technical_name: str,
        scope_key: str,
        entity_type: str | None = None,
        reason: str = "",
    ) -> TenantSchemaProposal:
        if self._session_factory is None:
            return self.create_schema_proposal(
                ctx,
                proposal_type=proposal_type,
                technical_name=technical_name,
                scope_key=scope_key,
                entity_type=entity_type,
                reason=reason,
            )
        proposal = self._build_proposal(
            ctx,
            proposal_type=proposal_type,
            technical_name=technical_name,
            scope_key=scope_key,
            entity_type=entity_type,
            reason=reason,
        )
        async with self._session_factory() as session, session.begin():
            session.add(_to_orm_proposal(proposal))
        return proposal

    # ------------------------------------------------------------------
    # Synchronous in-memory helpers (preserved for unit tests)
    # ------------------------------------------------------------------

    def _published_facts_for_tenant(self, tenant_id: str) -> list[dict[str, Any]]:
        today = datetime.now(UTC).date()
        facts: list[dict[str, Any]] = []
        for item in self._items.values():
            if item.tenant_id != tenant_id or item.status != KnowledgeItemStatus.PUBLISHED:
                continue
            if item.effective_to and item.effective_to < today:
                continue
            for idx, f in enumerate(item.accepted_facts):
                facts.append(
                    {
                        "fact_id": f.fact_id or f"{item.item_id}:{idx}",
                        "entity_type": f.entity_type,
                        "scope_key": f.scope_key,
                        "statement": f.statement,
                    }
                )
        return facts

    def create_draft(
        self,
        ctx: TrustedTenantContext,
        *,
        source_type: KnowledgeSourceType,
        scope_key: str,
        title: str,
        raw_text: str,
        candidate_facts: list[CandidateFact] | None = None,
        form_type: str | None = None,
        structured_payload: dict[str, Any] | None = None,
        effective_from: date | None = None,
        effective_to: date | None = None,
        visibility: VisibilityLevel = VisibilityLevel.PUBLIC,
        skip_scope_keyword_check: bool = False,
    ) -> KnowledgeItemRecord:
        record = self._build_draft_record(
            ctx,
            source_type=source_type,
            scope_key=scope_key,
            title=title,
            raw_text=raw_text,
            published_facts=self._published_facts_for_tenant(ctx.tenant_id),
            candidate_facts=candidate_facts,
            form_type=form_type,
            structured_payload=structured_payload,
            effective_from=effective_from,
            effective_to=effective_to,
            visibility=visibility,
            skip_scope_keyword_check=skip_scope_keyword_check,
        )
        self._items[record.item_id] = record
        return record

    def _get_for_tenant(self, ctx: TrustedTenantContext, item_id: str) -> KnowledgeItemRecord:
        item = self._items.get(item_id)
        if item is None or item.tenant_id != ctx.tenant_id:
            raise PermissionError("Knowledge item not found in current tenant.")
        return item

    def list_items(
        self, ctx: TrustedTenantContext, *, status: KnowledgeItemStatus | None = None
    ) -> list[KnowledgeItemRecord]:
        return [
            item
            for item in self._items.values()
            if item.tenant_id == ctx.tenant_id and (status is None or item.status == status)
        ]

    def get_item(self, ctx: TrustedTenantContext, item_id: str) -> KnowledgeItemRecord:
        return self._get_for_tenant(ctx, item_id)

    def transition(
        self,
        ctx: TrustedTenantContext,
        item_id: str,
        target_status: KnowledgeItemStatus,
        *,
        rejection_reason: str | None = None,
    ) -> KnowledgeItemRecord:
        item = self._get_for_tenant(ctx, item_id)
        self._validate_transition(
            ctx, item, target_status, self._published_facts_for_tenant(ctx.tenant_id)
        )
        item.status = target_status
        item.reviewer_user_id = ctx.user_id
        if rejection_reason:
            item.rejection_reason = rejection_reason
        item.updated_at = datetime.now(UTC)
        return item

    def create_new_version(
        self, ctx: TrustedTenantContext, item_id: str, *, raw_text: str, title: str | None = None
    ) -> KnowledgeItemRecord:
        old = self._get_for_tenant(ctx, item_id)
        new_rec = self.create_draft(
            ctx,
            source_type=old.source_type,
            scope_key=old.scope_key,
            title=title or old.title,
            raw_text=raw_text,
            effective_from=old.effective_from,
            effective_to=old.effective_to,
            visibility=old.visibility,
        )
        new_rec.version = old.version + 1
        new_rec.parent_item_id = old.item_id
        return new_rec

    def query_published(
        self,
        ctx: TrustedTenantContext,
        *,
        scope_key: str,
        agent_key: str | None = None,
        as_of: date | None = None,
    ) -> list[CandidateFact]:
        if agent_key and not self._caps.is_agent_allowed_scope(agent_key, scope_key):
            return []
        check_date = as_of or datetime.now(UTC).date()
        results: list[CandidateFact] = []
        for item in self._items.values():
            if item.tenant_id != ctx.tenant_id or item.status != KnowledgeItemStatus.PUBLISHED:
                continue
            if item.scope_key != scope_key or item.visibility != VisibilityLevel.PUBLIC:
                continue
            if item.effective_to is not None and item.effective_to < check_date:
                continue
            if item.effective_from is not None and item.effective_from > check_date:
                continue
            results.extend(item.accepted_facts)
        return results

    def _build_proposal(
        self,
        ctx: TrustedTenantContext,
        *,
        proposal_type: str,
        technical_name: str,
        scope_key: str,
        entity_type: str | None = None,
        reason: str = "",
    ) -> TenantSchemaProposal:
        decision = self._ownership.check_layer3_proposal(
            proposal_type,
            {"technicalName": technical_name, "entityType": entity_type or ""},
            tenant_id=ctx.tenant_id,
            approved=True,
        )
        if not decision.allowed:
            raise ValueError(decision.reason)
        return TenantSchemaProposal(
            tenant_id=ctx.tenant_id,
            proposal_type=proposal_type,
            technical_name=technical_name,
            entity_type=entity_type,
            scope_key=scope_key,
            reason=reason,
            author_user_id=ctx.user_id,
        )

    def create_schema_proposal(
        self,
        ctx: TrustedTenantContext,
        *,
        proposal_type: str,
        technical_name: str,
        scope_key: str,
        entity_type: str | None = None,
        reason: str = "",
    ) -> TenantSchemaProposal:
        proposal = self._build_proposal(
            ctx,
            proposal_type=proposal_type,
            technical_name=technical_name,
            scope_key=scope_key,
            entity_type=entity_type,
            reason=reason,
        )
        self._proposals[proposal.proposal_id] = proposal
        return proposal

    def review_schema_proposal(
        self,
        ctx: TrustedTenantContext,
        proposal_id: str,
        *,
        approve: bool,
        rejection_reason: str | None = None,
    ) -> TenantSchemaProposal:
        self._require_permission(
            ctx, KnowledgeItemStatus.PUBLISHED if approve else KnowledgeItemStatus.REJECTED
        )
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.tenant_id != ctx.tenant_id:
            raise PermissionError("Schema proposal not found in current tenant.")
        proposal.status = "APPROVED" if approve else "REJECTED"
        proposal.reviewed_by = ctx.user_id
        proposal.rejection_reason = rejection_reason
        return proposal

    def is_proposal_active(self, ctx: TrustedTenantContext, proposal_id: str) -> bool:
        proposal = self._proposals.get(proposal_id)
        return bool(
            proposal is not None
            and proposal.tenant_id == ctx.tenant_id
            and proposal.status == "APPROVED"
        )


def _to_orm_item(record: KnowledgeItemRecord) -> KnowledgeGovernanceItem:
    return KnowledgeGovernanceItem(
        item_id=record.item_id,
        tenant_id=record.tenant_id,
        source_type=record.source_type.value,
        scope_key=record.scope_key,
        title=record.title,
        raw_text=record.raw_text,
        version=record.version,
        parent_item_id=record.parent_item_id,
        status=record.status.value,
        visibility=record.visibility.value,
        author_user_id=record.author_user_id,
        reviewer_user_id=record.reviewer_user_id,
        rejection_reason=record.rejection_reason,
        form_type=record.form_type,
        structured_payload=record.structured_payload,
        evidence=record.evidence,
        validator_outcomes=[v.model_dump(mode="json") for v in record.validator_outcomes],
        accepted_facts=[f.model_dump(mode="json") for f in record.accepted_facts],
        rejected_facts=[r.model_dump(mode="json") for r in record.rejected_facts],
        conflicts=[c.model_dump(mode="json") for c in record.conflicts],
        quarantined_segments=record.quarantined_segments,
        effective_from=record.effective_from,
        effective_to=record.effective_to,
        created_at=record.created_at,
        updated_at=record.updated_at,
        processing_started_at=record.processing_started_at,
        published_at=record.published_at,
        last_error_code=record.last_error_code,
        last_error_message=record.last_error_message,
    )


def _from_orm_item(row: KnowledgeGovernanceItem) -> KnowledgeItemRecord:
    return KnowledgeItemRecord(
        item_id=row.item_id,
        tenant_id=row.tenant_id,
        source_type=KnowledgeSourceType(row.source_type),
        scope_key=row.scope_key,
        title=row.title,
        raw_text=row.raw_text,
        version=row.version,
        parent_item_id=row.parent_item_id,
        status=KnowledgeItemStatus(row.status),
        visibility=VisibilityLevel(row.visibility),
        author_user_id=row.author_user_id,
        reviewer_user_id=row.reviewer_user_id,
        rejection_reason=row.rejection_reason,
        form_type=row.form_type,
        structured_payload=row.structured_payload,
        evidence=list(row.evidence or []),
        validator_outcomes=[ValidatorOutcome.model_validate(v) for v in (row.validator_outcomes or [])],
        accepted_facts=[CandidateFact.model_validate(f) for f in (row.accepted_facts or [])],
        rejected_facts=[RejectedFact.model_validate(r) for r in (row.rejected_facts or [])],
        conflicts=[ConflictRecord.model_validate(c) for c in (row.conflicts or [])],
        quarantined_segments=list(row.quarantined_segments or []),
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        created_at=row.created_at,
        updated_at=row.updated_at,
        processing_started_at=row.processing_started_at,
        published_at=row.published_at,
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
    )


def _to_orm_proposal(proposal: TenantSchemaProposal) -> KnowledgeTenantProposal:
    return KnowledgeTenantProposal(
        proposal_id=proposal.proposal_id,
        tenant_id=proposal.tenant_id,
        proposal_type=proposal.proposal_type,
        technical_name=proposal.technical_name,
        entity_type=proposal.entity_type,
        scope_key=proposal.scope_key,
        reason=proposal.reason,
        status=proposal.status,
        author_user_id=proposal.author_user_id,
        reviewed_by=proposal.reviewed_by,
        rejection_reason=proposal.rejection_reason,
        created_at=proposal.created_at,
    )


__all__ = [
    "KnowledgeItemRecord",
    "KnowledgeItemStatus",
    "KnowledgeSourceType",
    "KnowledgeWorkflowService",
    "TenantSchemaProposal",
]
