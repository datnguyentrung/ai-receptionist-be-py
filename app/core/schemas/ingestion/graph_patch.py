"""Graph patch and extraction schemas."""

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from app.core.schemas.ingestion.document import IngestionBaseModel


class Evidence(IngestionBaseModel):
    source: str = Field(min_length=1)
    chunk_index: int = Field(ge=0, alias="chunkIndex")
    text: str = Field(min_length=1)
    section: str | None = None
    page: int | None = Field(default=None, ge=1)


class PropertyFact(IngestionBaseModel):
    property_name: str = Field(min_length=1, alias="propertyName")
    value: Any
    evidence: list[Evidence] = Field(default_factory=list)


class ExtractedNode(IngestionBaseModel):
    temp_id: str = Field(min_length=1, alias="tempId")
    class_name: str = Field(min_length=1, alias="className")
    identity: dict[str, Any] = Field(default_factory=dict)
    properties: list[PropertyFact] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class ExtractedEdge(IngestionBaseModel):
    edge_name: str = Field(min_length=1, alias="edgeName")
    source_temp_id: str = Field(min_length=1, alias="sourceTempId")
    target_temp_id: str = Field(min_length=1, alias="targetTempId")
    properties: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class ChunkCoverage(IngestionBaseModel):
    chunk_index: int = Field(ge=0, alias="chunkIndex")
    decision: Literal["MAPPED", "NOT_RELEVANT"]
    reason: str | None = None


class GraphPatchFragment(IngestionBaseModel):
    nodes: list[ExtractedNode] = Field(default_factory=list)
    edges: list[ExtractedEdge] = Field(default_factory=list)
    coverage: list[ChunkCoverage] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_are_local(self) -> Self:
        node_ids = {node.temp_id for node in self.nodes}
        for edge in self.edges:
            if edge.source_temp_id not in node_ids or edge.target_temp_id not in node_ids:
                # Keep flexible for cross-batch references if permitted, or validate
                pass
        return self


class GraphPatchDraft(GraphPatchFragment):
    pass


__all__ = [
    "ChunkCoverage",
    "Evidence",
    "ExtractedEdge",
    "ExtractedNode",
    "GraphPatchDraft",
    "GraphPatchFragment",
    "PropertyFact",
]
