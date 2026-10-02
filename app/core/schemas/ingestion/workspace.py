"""Workspace and batch schemas for ingestion session management."""

import hashlib
from typing import Any

from pydantic import Field

from app.core.schemas.ingestion.document import DocumentChunk, IngestionBaseModel
from app.core.schemas.ingestion.graph_patch import GraphPatchFragment


class IngestionProvenance(IngestionBaseModel):
    ontology_version: str = Field(alias="ontologyVersion")
    skill_digest: str = Field(default="skill-digest-v1", alias="skillDigest")
    model_id: str = Field(default="default-model", alias="modelId")

    def identity_material(self, artifact_name: str) -> str:
        return (
            f"{artifact_name}\0{self.ontology_version}\0"
            f"{self.skill_digest}\0{self.model_id}"
        )


class IngestionBatch(IngestionBaseModel):
    index: int = Field(ge=0)
    chunk_indexes: list[int] = Field(default_factory=list, alias="chunkIndexes")
    content_chars: int = Field(default=0, alias="contentChars")
    status: str = "PENDING"
    fragment: GraphPatchFragment | None = None
    scope_keys: list[str] = Field(default_factory=list, alias="scopeKeys")


class IngestionWorkspace(IngestionBaseModel):
    ingestion_id: str = Field(min_length=1, alias="ingestionId")
    artifact_name: str = Field(min_length=1, alias="artifactName")
    provenance: IngestionProvenance
    chunks: list[DocumentChunk]
    batches: list[IngestionBatch]
    validated_fingerprint: str | None = Field(
        default=None, alias="validatedFingerprint"
    )


__all__ = [
    "IngestionBatch",
    "IngestionProvenance",
    "IngestionWorkspace",
]
