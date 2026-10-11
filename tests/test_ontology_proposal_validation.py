import pytest

from app.models import SchemaProposalType
from app.services.ontology.ontology_lifecycle import (
    _next_version_code,
    _validate_proposal_payload,
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
