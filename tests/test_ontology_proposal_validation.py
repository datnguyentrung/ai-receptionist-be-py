import asyncio
import uuid
from types import SimpleNamespace

import pytest
from pydantic import TypeAdapter, ValidationError

from app.models import SchemaProposalType
from app.schemas.ingestion.proposal import SchemaProposalInput
from app.services.ontology.ontology_lifecycle import (
    OntologyLifecycle,
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


def test_entities_by_names_resolves_casefold_and_aliases() -> None:
    policy_entity = SimpleNamespace(
        id=uuid.uuid4(),
        technical_name="policy",
        display_name="Chính Sách",
    )
    alias_row = SimpleNamespace(
        entity_type_id=policy_entity.id,
        alias="QuyDinh",
    )

    class FakeScalars:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class FakeSession:
        def __init__(self):
            self.calls = 0

        async def scalars(self, _stmt):
            self.calls += 1
            return FakeScalars([policy_entity] if self.calls == 1 else [alias_row])

    resolved = asyncio.run(
        OntologyLifecycle._entities_by_names(
            FakeSession(),
            uuid.uuid4(),
            ["Policy", "policy", "Chính Sách", "QuyDinh"],
        )
    )
    assert [item.technical_name for item in resolved] == [
        "policy",
        "policy",
        "policy",
        "policy",
    ]
