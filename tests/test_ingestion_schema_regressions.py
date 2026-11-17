import inspect
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

from app.agent.tools.ingestion_tools import schema, workflow
from app.schemas.ingestion.proposal import SchemaProposalInput


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
