"""Public schemas for document ingestion, graph extraction, and ontology validation.

This package exposes unified contracts following Codebase Design (/codebase-design):
- `base`: IngestionModel, IngestionBaseModel, FreeformDict
- `document`: DocumentChunk, LoadedDocument, IngestionDocumentData, IngestionDocumentVersionData, IngestionChunkData
- `workspace`: IngestionProvenance, IngestionBatch, IngestionWorkspace, IngestionJobData, IngestionBatchData, Workspace
- `lifecycle`: IngestionJobStatus, SourceVersionStatus (from app.enums)
- `grounding`: Evidence, PropertyFact
- `semantic_patch`: SemanticGraphNode, SemanticGraphEdge, SemanticGraphPatchFragment
- `graph_patch`: GraphNode, GraphEdge, ExtractedNode, ExtractedEdge, GraphPatchFragment, GraphPatchDraft
- `coverage`: CoverageDecision, ChunkCoverage, ValidationIssue, PreparedChunk
- `ontology`: OntologyProjection, OntologyScopeSummary, ActiveOntology
"""

from app.schemas.ingestion.base import (
    FreeformDict,
    IngestionBaseModel,
    IngestionModel,
)
from app.schemas.ingestion.coverage import (
    ChunkCoverage,
    CoverageDecision,
    PreparedChunk,
    ValidationIssue,
)
from app.schemas.ingestion.document import (
    DocumentChunk,
    IngestionChunkData,
    IngestionDocumentData,
    IngestionDocumentVersionData,
    LoadedDocument,
)
from app.schemas.ingestion.graph_patch import (
    ExtractedEdge,
    ExtractedNode,
    GraphEdge,
    GraphNode,
    GraphPatchDraft,
    GraphPatchFragment,
)
from app.schemas.ingestion.grounding import Evidence, PropertyFact
from app.schemas.ingestion.ontology import (
    ActiveOntology,
    OntologyProjection,
    OntologyScopeSummary,
)
from app.schemas.ingestion.semantic_patch import (
    SemanticGraphEdge,
    SemanticGraphNode,
    SemanticGraphPatchFragment,
)
from app.schemas.ingestion.workspace import (
    IngestionBatch,
    IngestionBatchData,
    IngestionJobData,
    IngestionProvenance,
    IngestionWorkspace,
    Workspace,
)

__all__ = [
    "ActiveOntology",
    "ChunkCoverage",
    "CoverageDecision",
    "DocumentChunk",
    "Evidence",
    "ExtractedEdge",
    "ExtractedNode",
    "FreeformDict",
    "GraphEdge",
    "GraphNode",
    "GraphPatchDraft",
    "GraphPatchFragment",
    "IngestionBaseModel",
    "IngestionBatch",
    "IngestionBatchData",
    "IngestionChunkData",
    "IngestionDocumentData",
    "IngestionDocumentVersionData",
    "IngestionJobData",
    "IngestionModel",
    "IngestionProvenance",
    "IngestionWorkspace",
    "LoadedDocument",
    "OntologyProjection",
    "OntologyScopeSummary",
    "PreparedChunk",
    "PropertyFact",
    "SemanticGraphEdge",
    "SemanticGraphNode",
    "SemanticGraphPatchFragment",
    "ValidationIssue",
    "Workspace",
]
