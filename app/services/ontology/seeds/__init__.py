"""Layer 1 Business Ontology & Layer 2 Core Knowledge Ontology seeds."""

from app.services.ontology.seeds.layer1_business import (
    LAYER_1_ALIASES,
    LAYER_1_ENTITY_TYPES,
    LAYER_1_PROPERTIES,
    LAYER_1_RELATIONSHIPS,
)
from app.services.ontology.seeds.layer2_core import (
    LAYER_2_ALIASES,
    LAYER_2_ENTITY_TYPES,
    LAYER_2_PROPERTIES,
    LAYER_2_RELATIONSHIPS,
    LAYER_2_SCOPE_SPECS,
    TOOL_SCOPE_MAP,
)
from app.services.ontology.seeds.runner import ONTOLOGY_VERSION_CODE, seed_ontology

__all__ = [
    "LAYER_1_ALIASES",
    "LAYER_1_ENTITY_TYPES",
    "LAYER_1_PROPERTIES",
    "LAYER_1_RELATIONSHIPS",
    "LAYER_2_ALIASES",
    "LAYER_2_ENTITY_TYPES",
    "LAYER_2_PROPERTIES",
    "LAYER_2_RELATIONSHIPS",
    "LAYER_2_SCOPE_SPECS",
    "ONTOLOGY_VERSION_CODE",
    "TOOL_SCOPE_MAP",
    "seed_ontology",
]
