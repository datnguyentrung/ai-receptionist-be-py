from .ontology_alias import (
    OntologyAlias,
    OntologyAliasSource,
)
from .ontology_compiled_snapshot import OntologyCompiledSnapshot
from .ontology_entity_type import OntologyEntityType
from .ontology_property import (
    OntologyProperty,
    OntologyPropertyDataType,
)
from .ontology_relationship import (
    OntologyRelationship,
    RelationshipCardinality,
)
from .ontology_schema_proposal import (
    OntologySchemaProposal,
    SchemaProposalStatus,
    SchemaProposalType,
)
from .ontology_scope import OntologyEntityTypeScope, OntologyScope
from .ontology_version import (
    OntologyVersion,
    OntologyVersionStatus,
)

__all__ = [
    "OntologyAlias",
    "OntologyAliasSource",
    "OntologyCompiledSnapshot",
    "OntologyEntityType",
    "OntologyEntityTypeScope",
    "OntologyProperty",
    "OntologyPropertyDataType",
    "OntologyRelationship",
    "OntologySchemaProposal",
    "OntologyScope",
    "OntologyVersion",
    "OntologyVersionStatus",
    "RelationshipCardinality",
    "SchemaProposalStatus",
    "SchemaProposalType",
]
