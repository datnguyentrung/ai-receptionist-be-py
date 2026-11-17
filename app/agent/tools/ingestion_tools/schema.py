"""Ontology-schema proposal and ingestion rebase tools."""

from typing import Any

from google.adk.tools import ToolContext
from pydantic import TypeAdapter, ValidationError

from app.agent.tools.ingestion_tools.shared import (
    store_checkpoint,
    tool_error,
    tool_exception,
)
from app.agent.tools.ingestion_tools.state import (
    rebuild_batch_accumulator,
    workspace_payload,
)
from app.core.ingestion_runtime import get_service_container
from app.schemas.ingestion.proposal import SchemaProposalInput
from app.services.ingestion.engine import operations
from app.utils import log_ingestion_event

_SCHEMA_PROPOSAL_ADAPTER = TypeAdapter(SchemaProposalInput)


async def create_schema_proposal(
    batch_index: int,
    reason: str,
    affected_scope_keys: list[str],
    proposal: SchemaProposalInput,
    tool_context: ToolContext,
    ingestion_id: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    effective_ingestion_id = _resolve_ingestion_id(ingestion_id, tool_context)
    if not effective_ingestion_id:
        return tool_error(
            "schema_proposal",
            "INGESTION_ID_REQUIRED",
            "Provide ingestion_id or call begin_ingestion first",
        )
    request = {
        "ingestion_id": effective_ingestion_id,
        "batch_index": batch_index,
        "reason": reason,
        "affected_scope_keys": affected_scope_keys,
        "evidence": dict(evidence or {}),
    }
    try:
        proposal = _parse_schema_proposal(proposal)
        payload, technical_name = proposal.payload_dict(), proposal.technical_name
        if technical_name and "technicalName" not in payload:
            payload["technicalName"] = technical_name
        request.update(
            {
                "proposal_type": proposal.proposal_type,
                "technical_name": technical_name,
                "payload": payload,
            }
        )
        container = await get_service_container()
        workspace = await operations.required_workspace(
            container.repository, effective_ingestion_id
        )
        if (
            guarded := operations.workflow_guard_payload(
                workspace, "create_schema_proposal"
            )
        ) is not None:
            store_checkpoint(tool_context, guarded)
            log_ingestion_event(
                "CREATE_SCHEMA_PROPOSAL", payload=guarded, request=request
            )
            return guarded
        created = await container.ontology_lifecycle.create_proposal(
            ingestion_id=effective_ingestion_id,
            batch_index=batch_index,
            ontology_version_id=str(workspace.job.ontology_version_id),
            source_document_id=str(workspace.version.id),
            proposal_type=proposal.proposal_type,
            technical_name=technical_name,
            reason=reason,
            payload=payload,
            evidence=request["evidence"],
            affected_scope_keys=affected_scope_keys,
        )
        await container.repository.block_batch_for_proposal(
            effective_ingestion_id,
            batch_index,
            [
                {
                    "code": "SCHEMA_PROPOSAL_PENDING",
                    "message": f"Schema proposal {created.id} is waiting for review",
                    "location": f"batch[{batch_index}]",
                    "retryable": True,
                }
            ],
        )
        tool_context.state["pending_schema_proposal_id"] = str(created.id)
        result = {
            "success": True,
            "stage": "awaiting_schema_approval",
            "terminal": False,
            "retryRequired": False,
            "nextAction": "wait_for_user_review",
            "ingestionId": effective_ingestion_id,
            "batchIndex": batch_index,
            "proposalId": str(created.id),
            "proposalStatus": created.status.value,
        }
        log_ingestion_event("CREATE_SCHEMA_PROPOSAL", payload=result, request=request)
        return result
    except ValidationError as exc:
        result = tool_error("schema_proposal", "INVALID_SCHEMA_PROPOSAL", str(exc))
        log_ingestion_event("CREATE_SCHEMA_PROPOSAL", payload=result, request=request)
        return result
    except Exception as exc:  # noqa: BLE001
        result = tool_exception("schema_proposal", effective_ingestion_id, exc)
        store_checkpoint(tool_context, result)
        log_ingestion_event("CREATE_SCHEMA_PROPOSAL", payload=result, request=request)
        return result


def _parse_schema_proposal(proposal: SchemaProposalInput | dict[str, Any]) -> SchemaProposalInput:
    return _SCHEMA_PROPOSAL_ADAPTER.validate_python(proposal)


def _resolve_ingestion_id(
    ingestion_id: str | None, tool_context: ToolContext
) -> str:
    return str(
        ingestion_id or tool_context.state.get("active_ingestion_id", "")
    ).strip()


async def get_schema_proposal(
    proposal_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    del tool_context
    try:
        proposal = await (
            await get_service_container()
        ).ontology_lifecycle.get_proposal(proposal_id)
        if proposal is None:
            return tool_error("schema_proposal", "PROPOSAL_NOT_FOUND", proposal_id)
        return {
            "success": True,
            "stage": "schema_proposal",
            "proposalId": str(proposal.id),
            "proposalType": proposal.proposal_type.value,
            "proposalStatus": proposal.status.value,
            "reason": proposal.reason,
            "payload": proposal.payload,
            "evidence": proposal.evidence,
            "affectedScopeKeys": proposal.affected_scope_keys,
            "appliedOntologyVersionId": str(proposal.applied_ontology_version_id)
            if proposal.applied_ontology_version_id
            else None,
        }
    except Exception as exc:  # noqa: BLE001
        return tool_exception("schema_proposal", None, exc)


async def review_schema_proposal(
    proposal_id: str,
    approved: bool,
    tool_context: ToolContext,
    reviewed_by: str | None = None,
) -> dict[str, Any]:
    del reviewed_by
    confirmation = getattr(tool_context, "tool_confirmation", None)
    if confirmation is None or not confirmation.confirmed:
        return tool_error(
            "schema_proposal_review",
            "APPROVAL_REQUIRED",
            "Human confirmation is required",
        )
    effective_id = str(proposal_id).strip() or tool_context.state.get(
        "pending_schema_proposal_id", ""
    )
    try:
        container = await get_service_container()
        proposal = await container.ontology_lifecycle.review_proposal(
            effective_id, approved=approved, reviewed_by="human-confirmed"
        )
        tool_context.state["pending_schema_proposal_id"] = str(proposal.id)
        if (
            not approved
            and proposal.source_ingestion_id
            and proposal.source_batch_index is not None
        ):
            try:
                await container.repository.reject_batch_schema_proposal(
                    proposal.source_ingestion_id,
                    proposal.source_batch_index,
                    [
                        {
                            "code": "SCHEMA_PROPOSAL_REJECTED",
                            "message": f"Schema proposal {proposal.id} was rejected",
                            "location": f"batch[{proposal.source_batch_index}]",
                            "retryable": True,
                        }
                    ],
                )
            except KeyError:
                pass
        result = {
            "success": True,
            "stage": "schema_proposal_reviewed",
            "proposalId": str(proposal.id),
            "proposalStatus": proposal.status.value,
        }
        log_ingestion_event(
            "REVIEW_SCHEMA_PROPOSAL",
            payload=result,
            request={
                "proposal_id": effective_id,
                "approved": approved,
                "reviewed_by": "human-confirmed",
            },
        )
        return result
    except Exception as exc:  # noqa: BLE001
        return tool_exception("schema_proposal_review", None, exc)


async def apply_schema_proposal(
    proposal_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    effective_id = str(proposal_id).strip() or tool_context.state.get(
        "pending_schema_proposal_id", ""
    )
    try:
        version = await (
            await get_service_container()
        ).ontology_lifecycle.apply_proposal(
            effective_id, new_version_code=None, applied_by="human-confirmed"
        )
        result = {
            "success": True,
            "stage": "schema_draft_prepared",
            "proposalId": effective_id,
            "ontologyVersionId": str(version.id),
            "ontologyVersion": version.version,
            "nextAction": "rebase_ingestion",
        }
        log_ingestion_event(
            "APPLY_SCHEMA_PROPOSAL",
            payload=result,
            request={"proposal_id": effective_id, "draft_only": True},
        )
        return result
    except Exception as exc:  # noqa: BLE001
        return tool_exception("schema_proposal_apply", None, exc)


async def rebase_ingestion(
    ingestion_id: str, target_ontology_version_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    request = {
        "ingestion_id": ingestion_id,
        "target_ontology_version_id": target_ontology_version_id,
    }
    try:
        container = await get_service_container()
        current = await operations.required_workspace(
            container.repository, ingestion_id
        )
        if (
            guarded := operations.workflow_guard_payload(current, "rebase_ingestion")
        ) is not None:
            store_checkpoint(tool_context, guarded)
            return guarded
        active = await container.ontology_cache.active_version()
        if (
            target_ontology_version_id != active.version_id
            and not await container.ontology_lifecycle.is_ingestion_draft(
                ingestion_id, target_ontology_version_id
            )
        ):
            return tool_error(
                "ontology_rebase",
                "TARGET_ONTOLOGY_NOT_ALLOWED",
                "Rebase only to the current ACTIVE version or this job's DRAFT.",
            )
        await container.ontology_lifecycle.validate_identity_property_contracts(
            target_ontology_version_id
        )
        catalog = await container.ontology_cache.list_scopes(target_ontology_version_id)
        hashes = {
            item.scope_key: item.schema_hash for item in catalog if item.schema_hash
        }
        merged = {}
        for batch in current.batches:
            if batch.scope_keys and all(
                hashes.get(key) == batch.snapshot_hashes.get(key)
                for key in batch.scope_keys
            ):
                merged["\x1f".join(batch.scope_keys)] = (
                    await container.ontology_cache.get_many(
                        batch.scope_keys, target_ontology_version_id
                    )
                ).digest
        workspace = await container.repository.rebase_ontology_version(
            ingestion_id, target_ontology_version_id, hashes, merged
        )
        tool_context.state["active_ingestion_id"] = ingestion_id
        tool_context.state["active_ontology_scopes"] = []
        rebuild_batch_accumulator(tool_context, ingestion_id, workspace)
        result = workspace_payload(workspace)
        store_checkpoint(tool_context, result)
        log_ingestion_event("REBASE_INGESTION", payload=result, request=request)
        return result
    except Exception as exc:  # noqa: BLE001
        result = tool_exception("ontology_rebase", ingestion_id, exc)
        store_checkpoint(tool_context, result)
        log_ingestion_event("REBASE_INGESTION", payload=result, request=request)
        return result


__all__ = [
    "apply_schema_proposal",
    "create_schema_proposal",
    "get_schema_proposal",
    "rebase_ingestion",
    "review_schema_proposal",
]
