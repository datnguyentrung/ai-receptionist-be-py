"""Public contracts for Taekwondo document ingestion."""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema

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
    source: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    section: str | None = None
    page: int | None = Field(default=None, ge=1)


class PropertyFact(IngestionModel):
    property_name: str = Field(min_length=1)
    value: Any
    evidence: list[Evidence] = Field(min_length=1)


class SemanticGraphNode(IngestionModel):
    """LLM-facing node. Identity is deliberately absent."""

    temp_id: str = Field(min_length=1)
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


class ChunkCoverage(IngestionModel):
    chunk_index: int = Field(ge=0)
    decision: Literal["MAPPED", "NOT_RELEVANT"]
    reason: str = Field(min_length=1)


class SemanticGraphPatchFragment(IngestionModel):
    """The only graph-fragment contract exposed to the language model."""

    ontology_version: str = Field(min_length=1)
    nodes: list[SemanticGraphNode] = Field(default_factory=list)
    edges: list[SemanticGraphEdge] = Field(default_factory=list)
    coverage: list[ChunkCoverage] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


class GraphPatchFragment(IngestionModel):
    ontology_version: str = Field(min_length=1)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    coverage: list[ChunkCoverage] = Field(min_length=1)
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
    "Evidence",
    "FreeformDict",
    "GraphEdge",
    "GraphNode",
    "GraphPatchFragment",
    "IngestionJobStatus",
    "OntologyProjection",
    "OntologyScopeSummary",
    "PreparedChunk",
    "PropertyFact",
    "SemanticGraphEdge",
    "SemanticGraphNode",
    "SemanticGraphPatchFragment",
    "SourceVersionStatus",
    "ValidationIssue",
]
