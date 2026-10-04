"""Public contracts for Taekwondo document ingestion."""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, model_validator

# dict[str, Any] tự do nhưng JSON Schema không chứa additionalProperties
# (Gemini Developer API không hỗ trợ additionalProperties)
FreeformDict = Annotated[dict[str, Any], WithJsonSchema({"type": "object"})]


class IngestionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda name: "".join(
            word if index == 0 else word.capitalize()
            for index, word in enumerate(name.split("_"))
        ),
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )


class IngestionJobStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PARSING = "PARSING"
    DEDUPLICATING = "DEDUPLICATING"
    CLEANING = "CLEANING"
    VALIDATING_INPUT = "VALIDATING_INPUT"
    CHUNKING = "CHUNKING"
    BATCHING = "BATCHING"
    VALIDATING_GRAPH = "VALIDATING_GRAPH"
    READY = "READY"
    WRITING = "WRITING"
    VERIFYING = "VERIFYING"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    DELETED = "DELETED"
    ROLLED_BACK = "ROLLED_BACK"


class SourceVersionStatus(StrEnum):
    PENDING = "PENDING"
    WRITTEN = "WRITTEN"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"
    DELETED = "DELETED"
    ROLLED_BACK = "ROLLED_BACK"


class Evidence(IngestionModel):
    source: str = Field(default="document.md")
    chunk_index: int = Field(ge=0)
    evidence_ref: str | None = None
    text: str = Field(default="")
    section: str | None = None
    page: int | None = Field(default=None, ge=1)


EvidenceUnitKind = Literal["LINE", "BLOCK", "SECTION"]


class EvidenceUnit(IngestionModel):
    """Immutable source-owned evidence span exposed to the extractor by reference."""

    evidence_ref: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    kind: EvidenceUnitKind
    text: str = Field(min_length=1)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class PropertyFact(IngestionModel):
    property_name: str = Field(min_length=1)
    value: Any
    evidence: list[Evidence] = Field(min_length=1)


class SemanticGraphNode(IngestionModel):
    """LLM-facing node. Identity is deliberately absent."""

    temp_id: str = Field(default="")
    entity_ref: str | None = None
    class_name: str = Field(min_length=1)
    properties: list[PropertyFact] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class SemanticGraphEdge(IngestionModel):
    """LLM-facing edge with evidence-bearing property facts."""

    edge_name: str = Field(min_length=1)
    source_temp_id: str = Field(min_length=1)
    target_temp_id: str = Field(min_length=1)
    properties: list[PropertyFact] = Field(default_factory=list)
    evidence: list[Evidence] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)


class GraphNode(IngestionModel):
    temp_id: str = Field(min_length=1)
    class_name: str = Field(min_length=1)
    identity: FreeformDict = Field(default_factory=dict)
    properties: list[PropertyFact] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class GraphEdge(IngestionModel):
    edge_name: str = Field(min_length=1)
    source_temp_id: str = Field(min_length=1)
    target_temp_id: str = Field(min_length=1)
    properties: FreeformDict = Field(default_factory=dict)
    evidence: list[Evidence] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1)


class DuplicateFactClaim(IngestionModel):
    """Machine-checkable evidence that a chunk repeats an existing logical fact."""

    fact_ref: str = Field(min_length=1)
    evidence: Evidence


CoverageDecision = Literal[
    "MAPPED",
    "PARTIALLY_MAPPED",
    "NOT_RELEVANT",
    "NO_RELEVANT_FACT",
    "DUPLICATE_EVIDENCE",
    "UNSUPPORTED_BY_ONTOLOGY",
    "AMBIGUOUS",
    "FAILED",
    "SCHEMA_GAP",
]

class ChunkCoverage(IngestionModel):
    chunk_index: int = Field(ge=0)
    decision: CoverageDecision
    reason: str = Field(min_length=1)
    duplicate_claims: list[DuplicateFactClaim] = Field(default_factory=list)


class SemanticGraphPatchFragment(IngestionModel):
    """The only graph-fragment contract exposed to the language model."""

    nodes: list[SemanticGraphNode] = Field(default_factory=list)
    edges: list[SemanticGraphEdge] = Field(default_factory=list)
    coverage: list[ChunkCoverage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SemanticEntity(IngestionModel):
    """Declared entity within batch extraction."""

    temp_id: str = Field(default="")
    class_name: str = Field(min_length=1)
    entity_ref: str | None = None


class PropertyClaimMapping(IngestionModel):
    kind: Literal["PROPERTY"] = "PROPERTY"
    entity_ref: str = Field(min_length=1)
    property_name: str = Field(min_length=1)
    value: Any


class EdgeClaimMapping(IngestionModel):
    kind: Literal["EDGE"] = "EDGE"
    edge_name: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    properties: FreeformDict = Field(default_factory=dict)


ClaimMapping = Annotated[
    PropertyClaimMapping | EdgeClaimMapping,
    Field(discriminator="kind"),
]


class PropertySchemaGap(IngestionModel):
    kind: Literal["PROPERTY"] = "PROPERTY"
    entity_ref: str = Field(min_length=1)
    technical_name: str = Field(min_length=1)
    display_name: str = Field(default="")
    data_type: str = Field(default="STRING")
    value: Any = None
    reason: str = Field(default="")


class RelationshipSchemaGap(IngestionModel):
    kind: Literal["RELATIONSHIP"] = "RELATIONSHIP"
    technical_name: str = Field(min_length=1)
    display_name: str = Field(default="")
    source_ref: str = Field(min_length=1)
    target_ref: str = Field(min_length=1)
    cardinality: str = Field(default="MANY_TO_MANY")
    reason: str = Field(default="")


SchemaGapProposal = Annotated[
    PropertySchemaGap | RelationshipSchemaGap,
    Field(discriminator="kind"),
]

ClaimOutcome = Literal["MAPPED", "DUPLICATE", "SCHEMA_GAP", "AMBIGUOUS"]


class SemanticClaim(IngestionModel):
    """Atomic factual statement from a single chunk."""

    claim_id: str = Field(min_length=1)
    statement: str = Field(default="")
    evidence: Evidence
    outcome: ClaimOutcome = "MAPPED"
    mapping: PropertyClaimMapping | EdgeClaimMapping | None = None
    fact_ref: str | None = None
    schema_gap: PropertySchemaGap | RelationshipSchemaGap | None = None
    reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _infer_missing_outcome(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("outcome"):
            if data.get("mapping"):
                data["outcome"] = "MAPPED"
            elif data.get("schemaGap") or data.get("schema_gap"):
                data["outcome"] = "SCHEMA_GAP"
            elif data.get("factRef") or data.get("fact_ref"):
                data["outcome"] = "DUPLICATE"
            elif data.get("reason"):
                data["outcome"] = "AMBIGUOUS"
        return data


class ChunkLedger(IngestionModel):
    """Single source of truth accounting for all claims in one input chunk."""

    chunk_index: int = Field(ge=0)
    claims: list[SemanticClaim] = Field(default_factory=list)
    no_relevant_fact_reason: str | None = None


class SemanticBatchExtraction(IngestionModel):
    """The single-source-of-truth batch extraction contract."""

    entities: list[SemanticEntity] = Field(default_factory=list)
    chunks: list[ChunkLedger] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SemanticGraphRepairDelta(IngestionModel):
    """Additive-only repair input applied to a protected canonical baseline."""

    baseline_fingerprint: str = Field(min_length=1)
    nodes: list[SemanticGraphNode] = Field(default_factory=list)
    edges: list[SemanticGraphEdge] = Field(default_factory=list)
    coverage: list[ChunkCoverage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GraphPatchFragment(IngestionModel):
    ontology_version: str = Field(min_length=1)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    coverage: list[ChunkCoverage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ValidationIssue(IngestionModel):
    code: str
    message: str
    location: str | None = None
    retryable: bool = False


class PreparedChunk(IngestionModel):
    chunk_id: str
    chunk_index: int
    text: str
    content_hash: str
    token_count: int
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    source_anchor: str


class OntologyProjection(IngestionModel):
    version_id: str
    version: str
    digest: str
    scope_key: str
    scope_keys: list[str] = Field(default_factory=list)
    description: str = ""
    compiler_version: str = "ontology-compiler-v2"
    entity_types: list[FreeformDict]
    properties: list[FreeformDict]
    relationships: list[FreeformDict]
    aliases: list[FreeformDict]


class OntologyScopeSummary(IngestionModel):
    id: str
    ontology_version_id: str
    scope_key: str
    description: str
    summary: FreeformDict = Field(default_factory=dict)
    schema_hash: str | None = None


class ActiveOntology(IngestionModel):
    version_id: str
    version: str


__all__ = [
    "ActiveOntology",
    "ChunkCoverage",
    "ChunkLedger",
    "ClaimMapping",
    "ClaimOutcome",
    "DuplicateFactClaim",
    "EdgeClaimMapping",
    "Evidence",
    "EvidenceUnit",
    "EvidenceUnitKind",
    "FreeformDict",
    "GraphEdge",
    "GraphNode",
    "GraphPatchFragment",
    "IngestionJobStatus",
    "OntologyProjection",
    "OntologyScopeSummary",
    "PreparedChunk",
    "PropertyClaimMapping",
    "PropertyFact",
    "PropertySchemaGap",
    "RelationshipSchemaGap",
    "SchemaGapProposal",
    "SemanticBatchExtraction",
    "SemanticClaim",
    "SemanticEntity",
    "SemanticGraphEdge",
    "SemanticGraphNode",
    "SemanticGraphPatchFragment",
    "SemanticGraphRepairDelta",
    "SourceVersionStatus",
    "ValidationIssue",
]
