"""Document-level ingestion lifecycle and human-review tools."""

from typing import Any, Literal

from google.adk.tools import ToolContext

from app.agent.tools.ingestion_tools.shared import (
    store_checkpoint,
    tool_error,
    tool_exception,
)
from app.agent.tools.ingestion_tools.state import (
    finalize_result_payload,
    rebuild_batch_accumulator,
    workspace_payload,
)
from app.core.ingestion_runtime import get_service_container
from app.enums import IngestionJobStatus
from app.services.ingestion.engine import operations
from app.utils import log_ingestion_event


def _review_correction(
    *, stage: str, code: str, message: str, next_action: str
) -> dict[str, Any]:
    """Return a recoverable review error; these must not end the agent run."""
    return {
        "success": False,
        "stage": stage,
        "terminal": False,
        "retryRequired": False,
        "ingestionId": None,
        "nextAction": next_action,
        "errors": [{"code": code, "message": message, "retryable": True}],
    }


async def begin_ingestion(
    artifact_name: str,
    document_key: str,
    tool_context: ToolContext,
    scope_hint: str | None = None,
) -> dict[str, Any]:
    request = {
        "artifact_name": artifact_name,
        "document_key": document_key,
        "scope_hint": scope_hint,
    }
    try:
        container = await get_service_container()
        if resumed := await _resume_active_ingestion(
            container, artifact_name, document_key, tool_context
        ):
            store_checkpoint(tool_context, resumed)
            log_ingestion_event(
                f"BEGIN [{artifact_name}]", payload=resumed, request=request
            )
            return resumed
        artifact = await tool_context.load_artifact(artifact_name)
        if (
            artifact is None
            or artifact.inline_data is None
            or artifact.inline_data.data is None
        ):
            result = tool_error(
                "artifact_load",
                "ARTIFACT_NOT_FOUND",
                f"Artifact is unavailable in this ADK session: {artifact_name}",
            )
            log_ingestion_event(
                f"BEGIN [{artifact_name}]", payload=result, request=request
            )
            return result
        workspace = await operations.begin(
            container.repository,
            container.ontology_cache,
            artifact_name,
            bytes(artifact.inline_data.data),
            max_file_size=container.max_file_size,
            chunk_size_chars=container.chunk_size_chars,
            batch_size=container.batch_size,
            skill_digest=container.skill_digest,
            model_id=container.model_id,
            document_key=document_key,
            scope_hint=scope_hint,
            mime_type=artifact.inline_data.mime_type,
        )
        result = await _workspace_with_pending_proposals(container, workspace)
        if result.get("ingestionId"):
            tool_context.state["active_ingestion_id"] = result["ingestionId"]
            tool_context.state["active_ingestion_artifact_name"] = artifact_name
            tool_context.state["active_ingestion_document_key"] = document_key
            tool_context.state["ingestion_batch_cache"] = {}
            tool_context.state["ingestion_batch_accumulator"] = {
                "ingestionId": result["ingestionId"],
                "batches": {},
                "entities": {},
            }
        log_ingestion_event(f"BEGIN [{artifact_name}]", payload=result, request=request)
        return result
    except Exception as exc:  # noqa: BLE001
        result = tool_exception("artifact_load", None, exc)
        log_ingestion_event(f"BEGIN [{artifact_name}]", payload=result, request=request)
        return result


async def _resume_active_ingestion(
    container: Any,
    artifact_name: str,
    document_key: str,
    tool_context: ToolContext,
) -> dict[str, Any] | None:
    active_id = str(tool_context.state.get("active_ingestion_id", "")).strip()
    if not active_id:
        return None
    try:
        workspace = await operations.required_workspace(container.repository, active_id)
    except KeyError:
        return None

    status = str(workspace.job.status)
    if status in {"COMMITTED", "ABORTED", "FAILED", "DELETED", "ROLLED_BACK"}:
        return None
    same_document = (
        workspace.document.name == artifact_name
        and workspace.document.document_key == document_key
    )
    if not same_document:
        return {
            "success": False,
            "stage": "begin_ingestion",
            "terminal": False,
            "ingestionId": active_id,
            "nextAction": "resume_active_ingestion",
            "errors": [
                {
                    "code": "INGESTION_ALREADY_ACTIVE",
                    "message": "Finish, abort, or resume the active ingestion before starting another document.",
                }
            ],
        }
    return await _workspace_with_pending_proposals(container, workspace, resumed=True)


async def _workspace_with_pending_proposals(
    container: Any, workspace: Any, *, resumed: bool = False
) -> dict[str, Any]:
    result = workspace_payload(workspace, resumed=resumed, idempotent=resumed)
    result = _stale_baseline_action(result, workspace)
    if result["nextAction"] == "rebase_ingestion":
        return result
    if result["stage"] != "awaiting_schema_approval":
        return result
    schema_changes = await container.ontology_lifecycle.ingestion_schema_changes(
        str(workspace.job.id)
    )
    result = _schema_change_action(result, schema_changes)
    if result["nextAction"] != "wait_for_schema_review":
        return result
    pending_proposals = []
    for summary in schema_changes:
        if summary["status"] != "PROPOSED":
            continue
        proposal = await container.ontology_lifecycle.get_proposal(summary["proposalId"])
        if proposal is None:
            continue
        pending_proposals.append(
            {
                "proposalId": str(proposal.id),
                "proposalType": proposal.proposal_type.value,
                "proposalStatus": proposal.status.value,
                "reason": proposal.reason,
                "payload": proposal.payload,
                "evidence": proposal.evidence,
                "affectedScopeKeys": proposal.affected_scope_keys,
            }
        )
    if pending_proposals:
        result["pendingSchemaProposals"] = pending_proposals
        if len(pending_proposals) == 1:
            result["pendingSchemaProposal"] = pending_proposals[0]
    return result


def _stale_baseline_action(
    result: dict[str, Any], workspace: Any
) -> dict[str, Any]:
    """Require rebase when a batch baseline is pinned to an older ontology."""
    stale_batches = []
    current_version = str(workspace.job.ontology_version_id)
    for batch in workspace.batches:
        baseline = batch.validated_baseline
        if not isinstance(baseline, dict):
            continue
        baseline_version = str(
            baseline.get("ontologyVersion") or baseline.get("ontology_version") or ""
        )
        if baseline_version and baseline_version != current_version:
            stale_batches.append(batch.batch_index)
    if not stale_batches:
        return result

    result.update(
        {
            "success": False,
            "stage": "schema_rebase_required",
            "terminal": False,
            "retryRequired": True,
            "nextAction": "rebase_ingestion",
            "targetOntologyVersionId": current_version,
            "errors": [
                {
                    "code": "STALE_BASELINE_SCHEMA_VERSION",
                    "message": (
                        "Validated baseline belongs to an older ontology version; "
                        "rebase before processing batches"
                    ),
                    "location": f"batch[{stale_batches[0]}]",
                    "retryable": True,
                }
            ],
            "schemaRecovery": {
                "code": "STALE_BASELINE_SCHEMA_VERSION",
                "batchIndexes": stale_batches,
                "message": (
                    "Rebase to the job ontology version to clear stale repair state."
                ),
            },
        }
    )
    return result


def _schema_change_action(
    result: dict[str, Any], schema_changes: list[dict[str, Any]]
) -> dict[str, Any]:
    """Resolve stale schema-blocked state to the next actionable tool.

    Once a proposal has been applied, the batch keeps its historical
    SCHEMA_PROPOSAL_PENDING error until rebase resets it. Returning
    ``wait_for_schema_review`` in that state traps the workflow because there
    is no proposal left for the user to approve.
    """
    pending = [item for item in schema_changes if item["status"] == "PROPOSED"]
    result["pendingProposalCount"] = len(pending)
    if result.get("nextAction") != "wait_for_schema_review":
        return result
    if pending:
        return result

    applied_drafts = [
        item
        for item in schema_changes
        if item["status"] in {"APPROVED", "APPLIED"} and item.get("draftVersionId")
    ]
    if applied_drafts:
        target = applied_drafts[-1]
        result.update(
            {
                "stage": "schema_rebase_required",
                "nextAction": "rebase_ingestion",
                "targetOntologyVersionId": target["draftVersionId"],
                "schemaRecovery": {
                    "code": "APPLIED_SCHEMA_NOT_REBASED",
                    "proposalId": target["proposalId"],
                    "message": (
                        "The schema proposal was already applied; rebase the "
                        "ingestion before processing its batches again."
                    ),
                },
            }
        )
        return result

    approved_without_draft = [
        item for item in schema_changes if item["status"] == "APPROVED"
    ]
    if approved_without_draft:
        target = approved_without_draft[-1]
        result.update(
            {
                "stage": "schema_draft_required",
                "nextAction": "apply_schema_proposal",
                "proposalId": target["proposalId"],
                "schemaRecovery": {
                    "code": "APPROVED_SCHEMA_NOT_APPLIED",
                    "proposalId": target["proposalId"],
                    "message": (
                        "The schema proposal was approved but has not been "
                        "applied to the ingestion draft."
                    ),
                },
            }
        )
    return result


async def finalize_ingestion(
    ingestion_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    try:
        container = await get_service_container()
        workspace = await operations.finalize(
            container.repository, ingestion_id, container.graph_store
        )
        result = finalize_result_payload(workspace)
        if workspace.job.status == IngestionJobStatus.READY:
            result = await operations.build_preview(
                container.repository,
                container.graph_store,
                ingestion_id,
                await container.ontology_lifecycle.ingestion_schema_changes(
                    ingestion_id
                ),
                await container.ontology_lifecycle.active_version_id(),
            )
        store_checkpoint(tool_context, result)
        log_ingestion_event(
            "FINALIZE",
            payload={
                **result,
                "batchesForMerge": [
                    {
                        "batch_index": batch.batch_index,
                        "graph_fragment": batch.graph_fragment,
                    }
                    for batch in workspace.batches
                ],
            },
            request={"ingestion_id": ingestion_id},
        )
        return result
    except Exception as exc:  # noqa: BLE001
        result = tool_exception("finalize", ingestion_id, exc)
        store_checkpoint(tool_context, result)
        return result


async def get_ingestion_preview(
    ingestion_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    del tool_context
    try:
        return await (await get_service_container()).repository.get_preview(
            ingestion_id
        )
    except Exception as exc:  # noqa: BLE001
        return tool_exception("preview", ingestion_id, exc)


async def commit_ingestion(
    ingestion_id: str, report_digest: str, tool_context: ToolContext
) -> dict[str, Any]:
    try:
        container = await get_service_container()
        workspace = await container.repository.get_workspace(ingestion_id)
        if workspace is None:
            raise KeyError(ingestion_id)
        if workspace.job.status == IngestionJobStatus.AWAITING_COMMIT_APPROVAL:
            preview = await container.repository.get_preview(ingestion_id)
            if preview["pendingEntityReviewCount"]:
                return _review_correction(
                    stage="awaiting_commit_approval",
                    code="REVIEW_REQUIRED",
                    message="Resolve every entity review before committing",
                    next_action="review_entity_resolutions",
                )
            if preview["pendingConflictCount"]:
                return _review_correction(
                    stage="awaiting_commit_approval",
                    code="REVIEW_REQUIRED",
                    message="Resolve every fact conflict before committing",
                    next_action="review_ingestion_conflicts",
                )
            if preview["reportDigest"] != report_digest:
                return _review_correction(
                    stage="commit_approval",
                    code="PREVIEW_STALE",
                    message="Preview changed; read the latest preview and ask for confirmation again",
                    next_action="get_ingestion_preview",
                )
            try:
                await container.repository.record_commit_approval(
                    ingestion_id,
                    report_digest,
                    user_id=tool_context.user_id,
                    session_id=tool_context.session.id,
                    function_call_id=tool_context.function_call_id or "",
                )
            except ValueError as exc:
                return _review_correction(
                    stage="commit_approval",
                    code="REVIEW_REQUIRED",
                    message=str(exc),
                    next_action="get_ingestion_preview",
                )
        result = await operations.commit(
            container.repository,
            container.graph_store,
            ingestion_id,
            report_digest,
            container.ontology_lifecycle,
        )
        store_checkpoint(tool_context, result)
        log_ingestion_event(
            "COMMIT_INGESTION",
            payload=result,
            request={"ingestion_id": ingestion_id, "report_digest": report_digest},
        )
        return result
    except Exception as exc:  # noqa: BLE001
        return tool_exception("commit", ingestion_id, exc)


async def abort_ingestion(ingestion_id: str) -> dict[str, Any]:
    try:
        container = await get_service_container()
        workspace = await container.repository.abort(ingestion_id)
        await container.ontology_lifecycle.discard_ingestion_draft(ingestion_id)
        return operations.status_payload(workspace)
    except Exception as exc:  # noqa: BLE001
        return tool_exception("abort", ingestion_id, exc)


async def get_ingestion_status(
    ingestion_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    try:
        container = await get_service_container()
        workspace = await operations.required_workspace(
            container.repository, ingestion_id
        )
        result = operations.status_payload(workspace)
        result = _stale_baseline_action(result, workspace)
        schema_changes = await container.ontology_lifecycle.ingestion_schema_changes(
            ingestion_id
        )
        result = _schema_change_action(result, schema_changes)
        result["pendingConflictCount"] = sum(
            item["decision"] == "PENDING"
            for item in await container.repository.list_fact_conflicts(ingestion_id)
        )
        result["pendingEntityReviewCount"] = (
            await container.repository.pending_entity_resolution_reviews(ingestion_id)
        )
        if result.get("stage") == "awaiting_commit_approval":
            result["nextAction"] = (
                "review_entity_resolutions"
                if result["pendingEntityReviewCount"]
                else "review_ingestion_conflicts"
                if result["pendingConflictCount"]
                else "confirm_and_commit"
            )
        result["reportDigest"] = workspace.job.summary.get("reportDigest")
        store_checkpoint(tool_context, result)
        return result
    except Exception as exc:  # noqa: BLE001
        result = tool_exception("status", ingestion_id, exc)
        store_checkpoint(tool_context, result)
        return result


async def choose_ingestion_recovery(
    ingestion_id: str,
    strategy: Literal["REPAIR", "REEXTRACT"],
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Apply the user's recovery choice for the next repair-required batch."""
    try:
        container = await get_service_container()
        workspace = await operations.required_workspace(
            container.repository, ingestion_id
        )
        batch = next(
            (item for item in workspace.batches if item.status != "STAGED"), None
        )
        if batch is None or batch.status != "REPAIR_REQUIRED":
            return {
                "success": False,
                "stage": "ingestion_recovery",
                "terminal": False,
                "ingestionId": ingestion_id,
                "nextAction": "get_ingestion_status",
                "errors": [
                    {
                        "code": "RECOVERY_NOT_REQUIRED",
                        "message": "The next batch is not awaiting repair.",
                    }
                ],
            }

        options = operations.recovery_options(batch)
        option = next((item for item in options if item["strategy"] == strategy), None)
        if option is None:
            return {
                "success": False,
                "stage": "repair_required",
                "terminal": False,
                "ingestionId": ingestion_id,
                "batchIndex": batch.batch_index,
                "nextAction": "choose_ingestion_recovery",
                "recoveryOptions": options,
                "errors": [
                    {
                        "code": "INVALID_RECOVERY_STRATEGY",
                        "message": "Choose either REPAIR or REEXTRACT.",
                    }
                ],
            }
        if not option["available"]:
            return {
                "success": False,
                "stage": "repair_required",
                "terminal": False,
                "ingestionId": ingestion_id,
                "batchIndex": batch.batch_index,
                "nextAction": "choose_ingestion_recovery",
                "recoveryOptions": options,
                "errors": [
                    {
                        "code": "RECOVERY_STRATEGY_UNAVAILABLE",
                        "message": option["unavailableReason"],
                    }
                ],
            }

        if strategy == "REEXTRACT":
            workspace = await container.repository.restart_from_batch(
                ingestion_id,
                batch.batch_index,
                "User requested re-extraction after validation failure.",
            )
            rebuild_batch_accumulator(tool_context, ingestion_id, workspace)
            tool_context.state["ingestion_recovery"] = None
            result = workspace_payload(workspace)
        else:
            tool_context.state["ingestion_recovery"] = {
                "ingestionId": ingestion_id,
                "batchIndex": batch.batch_index,
                "strategy": strategy,
            }
            result = workspace_payload(workspace)
            result["recoverySelected"] = strategy

        store_checkpoint(tool_context, result)
        log_ingestion_event(
            "CHOOSE_INGESTION_RECOVERY",
            payload=result,
            request={"ingestion_id": ingestion_id, "strategy": strategy},
        )
        return result
    except Exception as exc:  # noqa: BLE001
        return tool_exception("ingestion_recovery", ingestion_id, exc)


async def list_ingestion_conflicts(
    ingestion_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    del tool_context
    try:
        repository = (await get_service_container()).repository
        conflicts = await repository.list_fact_conflicts(ingestion_id)
        entity_resolutions = await repository.list_entity_resolution_reviews(
            ingestion_id
        )
        return {
            "success": True,
            "ingestionId": ingestion_id,
            "conflicts": conflicts,
            "entityResolutions": entity_resolutions,
            "pendingConflictCount": sum(
                item["decision"] == "PENDING" for item in conflicts
            ),
            "pendingEntityReviewCount": sum(
                item["decision"] == "PENDING" for item in entity_resolutions
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return tool_exception("conflict_list", ingestion_id, exc)


async def review_ingestion_conflict(
    conflict_id: str,
    decision: Literal["CONFIRM", "REJECT", "OTHER"],
    tool_context: ToolContext,
    note: str | None = None,
) -> dict[str, Any]:
    try:
        container = await get_service_container()
        try:
            conflict = await container.repository.review_fact_conflict(
                conflict_id, decision, note
            )
        except KeyError:
            if await container.repository.entity_resolution_review_exists(conflict_id):
                return _review_correction(
                    stage="conflict_review",
                    code="WRONG_REVIEW_TOOL",
                    message=(
                        "This is an entity resolution review ID; "
                        "call review_entity_resolutions with REUSE or DISTINCT"
                    ),
                    next_action="review_entity_resolutions",
                )
            return _review_correction(
                stage="conflict_review",
                code="REVIEW_ID_NOT_FOUND",
                message="Fact conflict ID was not found; read the latest preview",
                next_action="get_ingestion_preview",
            )
        except ValueError as exc:
            return _review_correction(
                stage="conflict_review",
                code="INVALID_REVIEW_DECISION",
                message=str(exc),
                next_action="review_ingestion_conflicts",
            )
        preview = await container.repository.get_preview(str(conflict["ingestionId"]))
        return {
            "success": True,
            "conflict": conflict,
            "ingestionId": conflict["ingestionId"],
            "reportDigest": preview["reportDigest"],
            "pendingConflictCount": preview["pendingConflictCount"],
            "pendingEntityReviewCount": preview["pendingEntityReviewCount"],
            "nextAction": preview["nextAction"],
        }
    except Exception as exc:  # noqa: BLE001
        return tool_exception("conflict_review", None, exc)


async def review_ingestion_conflicts(
    ingestion_id: str,
    decision: Literal["CONFIRM", "REJECT", "OTHER"],
    tool_context: ToolContext,
    note: str | None = None,
) -> dict[str, Any]:
    """Resolve all pending fact conflicts with one confirmed decision."""
    try:
        container = await get_service_container()
        conflicts = await container.repository.review_fact_conflicts(
            ingestion_id, decision, note
        )
        preview = await container.repository.get_preview(ingestion_id)
        return {
            "success": True,
            "ingestionId": ingestion_id,
            "updatedConflictIds": [item["conflictId"] for item in conflicts],
            "updatedConflictCount": len(conflicts),
            "reportDigest": preview["reportDigest"],
            "pendingConflictCount": preview["pendingConflictCount"],
            "pendingEntityReviewCount": preview["pendingEntityReviewCount"],
            "nextAction": preview["nextAction"],
        }
    except ValueError as exc:
        return _review_correction(
            stage="conflict_reviews",
            code="INVALID_REVIEW_DECISION",
            message=str(exc),
            next_action="review_ingestion_conflicts",
        )
    except Exception as exc:  # noqa: BLE001
        return tool_exception("conflict_reviews", ingestion_id, exc)


async def review_entity_resolution(
    review_id: str,
    decision: Literal["REUSE", "DISTINCT"],
    tool_context: ToolContext,
    note: str | None = None,
) -> dict[str, Any]:
    try:
        container = await get_service_container()
        try:
            review = await container.repository.review_entity_resolution(
                review_id, decision, note
            )
        except KeyError:
            if await container.repository.fact_conflict_exists(review_id):
                return _review_correction(
                    stage="entity_review",
                    code="WRONG_REVIEW_TOOL",
                    message=(
                        "This is a fact conflict ID; "
                        "call review_ingestion_conflicts instead"
                    ),
                    next_action="review_ingestion_conflicts",
                )
            return _review_correction(
                stage="entity_review",
                code="REVIEW_ID_NOT_FOUND",
                message="Entity review ID was not found; read the latest preview",
                next_action="get_ingestion_preview",
            )
        except ValueError as exc:
            return _review_correction(
                stage="entity_review",
                code="INVALID_REVIEW_DECISION",
                message=str(exc),
                next_action="review_entity_resolutions",
            )
        ingestion_id = str(review["ingestionId"])
        preview = await operations.build_preview(
            container.repository,
            container.graph_store,
            ingestion_id,
            await container.ontology_lifecycle.ingestion_schema_changes(ingestion_id),
            await container.ontology_lifecycle.active_version_id(),
        )
        return {
            "success": True,
            "ingestionId": ingestion_id,
            "entityResolution": review,
            "reportDigest": preview["reportDigest"],
            "pendingConflictCount": preview["pendingConflictCount"],
            "pendingEntityReviewCount": preview["pendingEntityReviewCount"],
            "nextAction": preview["nextAction"],
        }
    except Exception as exc:  # noqa: BLE001
        return tool_exception("entity_review", None, exc)


async def review_entity_resolutions(
    ingestion_id: str,
    decision: Literal["REUSE", "DISTINCT"],
    tool_context: ToolContext,
    note: str | None = None,
) -> dict[str, Any]:
    """Resolve all pending entity reviews with one confirmed decision."""
    try:
        container = await get_service_container()
        reviews = await container.repository.review_entity_resolutions(
            ingestion_id, decision, note
        )
        preview = await operations.build_preview(
            container.repository,
            container.graph_store,
            ingestion_id,
            await container.ontology_lifecycle.ingestion_schema_changes(ingestion_id),
            await container.ontology_lifecycle.active_version_id(),
        )
        return {
            "success": True,
            "ingestionId": ingestion_id,
            "updatedReviewIds": [item["reviewId"] for item in reviews],
            "updatedReviewCount": len(reviews),
            "reportDigest": preview["reportDigest"],
            "pendingConflictCount": preview["pendingConflictCount"],
            "pendingEntityReviewCount": preview["pendingEntityReviewCount"],
            "nextAction": preview["nextAction"],
        }
    except ValueError as exc:
        return _review_correction(
            stage="entity_reviews",
            code="INVALID_REVIEW_DECISION",
            message=str(exc),
            next_action="review_entity_resolutions",
        )
    except Exception as exc:  # noqa: BLE001
        return tool_exception("entity_reviews", ingestion_id, exc)


async def fill_ingestion(
    ingestion_id: str, tool_context: ToolContext
) -> dict[str, Any]:
    del ingestion_id, tool_context
    return tool_error(
        "commit_approval",
        "APPROVAL_REQUIRED",
        "Use the human-confirmed commit_ingestion tool with the preview digest",
    )


__all__ = [
    "abort_ingestion",
    "begin_ingestion",
    "commit_ingestion",
    "fill_ingestion",
    "finalize_ingestion",
    "get_ingestion_preview",
    "get_ingestion_status",
    "list_ingestion_conflicts",
    "review_entity_resolution",
    "review_entity_resolutions",
    "review_ingestion_conflict",
    "review_ingestion_conflicts",
]
