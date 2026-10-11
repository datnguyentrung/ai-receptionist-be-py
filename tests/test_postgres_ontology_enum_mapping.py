"""The existing configured database stores ontology enum types in public."""

from app.models.ontology_alias import OntologyAlias
from app.models.ontology_property import OntologyProperty
from app.models.ontology_relationship import OntologyRelationship
from app.models.ontology_schema_proposal import OntologySchemaProposal
from app.models.ontology_version import OntologyVersion


def test_ontology_enum_types_use_existing_public_schema() -> None:
    for model, column in (
        (OntologyVersion, "status"),
        (OntologyAlias, "source"),
        (OntologyProperty, "data_type"),
        (OntologyRelationship, "cardinality"),
        (OntologySchemaProposal, "proposal_type"),
        (OntologySchemaProposal, "status"),
    ):
        assert model.__table__.c[column].type.schema == "public"
