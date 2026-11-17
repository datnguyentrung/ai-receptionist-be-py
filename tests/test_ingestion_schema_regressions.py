import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter, ValidationError

from app.agent.tools.ingestion_tools import schema, workflow
from app.models import SchemaProposalType
from app.schemas.ingestion.proposal import SchemaProposalInput
from app.services.ingestion.engine.workflow_policy import evaluate_workflow
from app.services.ontology.ontology_lifecycle import (
    SchemaObjectAlreadyExistsError,
)


def test_new_entity_declares_every_identity_property() -> None:
    adapter = TypeAdapter(SchemaProposalInput)
    request = {
        "proposal_type": "NEW_ENTITY_TYPE",
        "technical_name": "Coach",
        "payload": {
            "identityStrategy": {"required": ["name"], "sourceScoped": True},
            "properties": [{"technicalName": "name", "dataType": "STRING"}],
        },
    }

    proposal = adapter.validate_python(request)

    assert proposal.payload.properties[0].technical_name == "name"


def test_new_entity_rejects_undeclared_identity_property() -> None:
    adapter = TypeAdapter(SchemaProposalInput)
    request = {
        "proposal_type": "NEW_ENTITY_TYPE",
        "technical_name": "Coach",
        "payload": {
            "identityStrategy": {"required": ["name"], "sourceScoped": True},
        },
    }

    with pytest.raises(ValidationError, match="identity properties must be declared: name"):
        adapter.validate_python(request)


def test_create_schema_proposal_uses_active_ingestion_id() -> None:
    context = SimpleNamespace(state={"active_ingestion_id": "ingestion-1"})
    signature = inspect.signature(schema.create_schema_proposal)

    assert signature.parameters["ingestion_id"].default is None
    assert schema._resolve_ingestion_id(None, context) == "ingestion-1"
    assert schema._resolve_ingestion_id("explicit-id", context) == "explicit-id"


def test_schema_proposal_tool_normalizes_adk_dict_input() -> None:
    proposal = schema._parse_schema_proposal(
        {
            "proposalType": "NEW_PROPERTY",
            "technicalName": "recruitment_remuneration",
            "payload": {"entityType": "Coach", "dataType": "STRING"},
        }
    )

    assert proposal.proposal_type == "NEW_PROPERTY"
    assert proposal.payload_dict()["entityType"] == "Coach"


def test_begin_ingestion_checks_active_job_before_loading_artifact() -> None:
    source = inspect.getsource(workflow.begin_ingestion)

    assert source.index("_resume_active_ingestion") < source.index("load_artifact")
    assert "ingestion_batch_cache" not in source.split("_resume_active_ingestion", 1)[0]


def test_applied_schema_recovery_requests_rebase() -> None:
    draft_version_id = str(uuid.uuid4())
    result = workflow._schema_change_action(
        {
            "success": True,
            "stage": "awaiting_schema_approval",
            "nextAction": "wait_for_schema_review",
            "validationErrors": [
                {
                    "code": "SCHEMA_PROPOSAL_PENDING",
                    "message": "Schema proposal is waiting for review",
                }
            ],
        },
        [
            {
                "proposalId": str(uuid.uuid4()),
                "status": "APPROVED",
                "draftVersionId": draft_version_id,
            }
        ],
    )

    assert result["stage"] == "schema_rebase_required"
    assert result["nextAction"] == "rebase_ingestion"
    assert result["targetOntologyVersionId"] == draft_version_id
    assert result["schemaRecovery"]["code"] == "APPLIED_SCHEMA_NOT_REBASED"
    assert result["pendingProposalCount"] == 0


def test_proposed_schema_keeps_waiting_for_review() -> None:
    result = workflow._schema_change_action(
        {
            "success": True,
            "stage": "awaiting_schema_approval",
            "nextAction": "wait_for_schema_review",
        },
        [
            {
                "proposalId": str(uuid.uuid4()),
                "status": "PROPOSED",
                "draftVersionId": None,
            }
        ],
    )

    assert result["nextAction"] == "wait_for_schema_review"
    assert result["pendingProposalCount"] == 1


def test_stale_baseline_requests_rebase_before_batch_processing() -> None:
    current_version = str(uuid.uuid4())
    result = workflow._stale_baseline_action(
        {
            "success": False,
            "stage": "explicit_extraction_failure",
            "terminal": True,
            "retryRequired": False,
            "nextAction": "explicit_extraction_failure",
        },
        SimpleNamespace(
            job=SimpleNamespace(ontology_version_id=current_version),
            batches=[
                SimpleNamespace(
                    batch_index=0,
                    validated_baseline={
                        "ontologyVersion": str(uuid.uuid4()),
                    },
                )
            ],
        ),
    )

    assert result["stage"] == "schema_rebase_required"
    assert result["terminal"] is False
    assert result["nextAction"] == "rebase_ingestion"
    assert result["targetOntologyVersionId"] == current_version
    assert result["schemaRecovery"]["batchIndexes"] == [0]


def test_failed_job_allows_rebase_recovery() -> None:
    decision = evaluate_workflow("FAILED", "rebase_ingestion")

    assert decision.allowed is True
    assert decision.terminal is True


@pytest.mark.anyio
async def test_apply_schema_proposal_returns_single_uuid_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version_id = uuid.uuid4()
    lifecycle = SimpleNamespace(
        apply_proposal=AsyncMock(
            return_value=SimpleNamespace(
                id=version_id,
                version="draft-should-not-be-exposed",
            )
        )
    )
    monkeypatch.setattr(
        schema,
        "get_service_container",
        AsyncMock(return_value=SimpleNamespace(ontology_lifecycle=lifecycle)),
    )
    monkeypatch.setattr(schema, "log_ingestion_event", lambda *args, **kwargs: None)
    context = SimpleNamespace(state={})

    result = await schema.apply_schema_proposal(str(uuid.uuid4()), context)

    assert result["targetOntologyVersionId"] == str(version_id)
    assert "ontologyVersionId" not in result
    assert "ontologyVersion" not in result


@pytest.mark.anyio
async def test_approved_proposal_returns_apply_next_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal_id = uuid.uuid4()
    lifecycle = SimpleNamespace(
        review_proposal=AsyncMock(
            return_value=SimpleNamespace(
                id=proposal_id,
                status=SimpleNamespace(value="APPROVED"),
                source_ingestion_id=None,
                source_batch_index=None,
            )
        )
    )
    monkeypatch.setattr(
        schema,
        "get_service_container",
        AsyncMock(return_value=SimpleNamespace(ontology_lifecycle=lifecycle)),
    )
    monkeypatch.setattr(schema, "log_ingestion_event", lambda *args, **kwargs: None)
    context = SimpleNamespace(
        state={},
        tool_confirmation=SimpleNamespace(confirmed=True),
    )

    result = await schema.review_schema_proposal(
        str(proposal_id),
        True,
        context,
    )

    assert result["proposalStatus"] == "APPROVED"
    assert result["terminal"] is False
    assert result["nextAction"] == "apply_schema_proposal"


@pytest.mark.anyio
async def test_rebase_ingestion_rejects_version_code_before_service_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_container = AsyncMock()
    monkeypatch.setattr(schema, "get_service_container", get_container)
    context = SimpleNamespace(state={})

    result = await schema.rebase_ingestion(
        str(uuid.uuid4()),
        "draft-53edd2238a2f45c7b4eecf838d839f25",
        context,
    )

    assert result["success"] is False
    assert result["terminal"] is True
    assert result["nextAction"] == "report_tool_failure"
    assert "target_ontology_version_id must be a UUID" in result["errors"][0]["message"]
    get_container.assert_not_awaited()


@pytest.mark.anyio
async def test_create_schema_proposal_recovers_when_schema_object_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingestion_id = str(uuid.uuid4())
    version_id = uuid.uuid4()
    lifecycle = SimpleNamespace(
        create_proposal=AsyncMock(
            side_effect=SchemaObjectAlreadyExistsError(
                SchemaProposalType.NEW_ENTITY_TYPE,
                "requirement",
            )
        )
    )
    repository = SimpleNamespace(block_batch_for_proposal=AsyncMock())
    monkeypatch.setattr(
        schema,
        "get_service_container",
        AsyncMock(
            return_value=SimpleNamespace(
                ontology_lifecycle=lifecycle,
                repository=repository,
            )
        ),
    )
    monkeypatch.setattr(
        schema.operations,
        "required_workspace",
        AsyncMock(
            return_value=SimpleNamespace(
                job=SimpleNamespace(ontology_version_id=version_id),
                version=SimpleNamespace(id=uuid.uuid4()),
            )
        ),
    )
    monkeypatch.setattr(
        schema.operations,
        "workflow_guard_payload",
        lambda *_args, **_kwargs: None,
    )
    blocked_workspace = SimpleNamespace(
        job=SimpleNamespace(ontology_version_id=version_id)
    )
    repository.block_batch_for_proposal.return_value = blocked_workspace
    monkeypatch.setattr(schema, "log_ingestion_event", lambda *args, **kwargs: None)
    context = SimpleNamespace(state={})

    result = await schema.create_schema_proposal(
        batch_index=0,
        reason="schema gap",
        affected_scope_keys=["training"],
        proposal={
            "proposalType": "NEW_ENTITY_TYPE",
            "technicalName": "requirement",
            "payload": {
                "identityStrategy": {
                    "sourceScoped": True,
                    "required": ["name"],
                },
                "properties": [
                    {"technicalName": "name", "dataType": "STRING"}
                ],
            },
        },
        tool_context=context,
        ingestion_id=ingestion_id,
    )

    assert result["success"] is False
    assert result["terminal"] is False
    assert result["nextAction"] == "rebase_ingestion"
    assert result["targetOntologyVersionId"] == str(version_id)
    assert result["errors"][0]["code"] == "SCHEMA_OBJECT_ALREADY_EXISTS"
    repository.block_batch_for_proposal.assert_awaited_once()
