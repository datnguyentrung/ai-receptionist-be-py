import pytest
from pydantic import TypeAdapter, ValidationError

from app.models import SchemaProposalType
from app.schemas.ingestion.proposal import SchemaProposalInput
from app.services.ontology.ontology_lifecycle import (
    _next_version_code,
    _validate_proposal_payload,
    identity_property_contract_errors,
)


def test_service_allocates_patch_version_and_rejects_malformed_property() -> None:
    assert _next_version_code("v1.2.3") == "v1.2.4"
    _validate_proposal_payload(
        SchemaProposalType.NEW_PROPERTY,
        {"technicalName": "rank", "entityType": "student", "dataType": "STRING"},
        ["training"],
    )
    with pytest.raises(ValueError, match="entityType"):
        _validate_proposal_payload(
            SchemaProposalType.NEW_PROPERTY,
            {"technicalName": "rank", "dataType": "STRING"},
            ["training"],
        )


def test_identity_strategy_must_reference_declared_properties() -> None:
    entities = [{"technicalName": "record", "identityStrategy": {"required": ["code", "year"]}}]
    assert identity_property_contract_errors(entities, {"record": {"code"}}) == ["record.year"]
    assert identity_property_contract_errors(entities, {"record": {"code", "year"}}) == []


def test_schema_proposal_tool_requires_typed_property_and_entity_payloads() -> None:
    adapter = TypeAdapter(SchemaProposalInput)

    property_proposal = adapter.validate_python(
        {
            "proposal_type": "NEW_PROPERTY",
            "technical_name": "recruitment_remuneration",
            "payload": {"entityType": "Coach", "dataType": "STRING"},
        }
    )
    assert property_proposal.payload.data_type == "STRING"

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "proposal_type": "NEW_PROPERTY",
                "technical_name": "recruitment_remuneration",
                "payload": {"entityType": "Coach", "dataType": "string"},
            }
        )

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "proposal_type": "NEW_ENTITY_TYPE",
                "technical_name": "RemunerationPolicy",
                "payload": {"description": "Missing identity strategy"},
            }
        )
