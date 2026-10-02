"""Schemas for document chunks, loaded sources, and ingestion provenance."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IngestionBaseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda name: "".join(
            word if index == 0 else word.capitalize()
            for index, word in enumerate(name.split("_"))
        ),
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )


class DocumentChunk(IngestionBaseModel):
    index: int = Field(ge=0)
    source: str = Field(min_length=1)
    section: str | None = None
    content: str = Field(min_length=1)
    document_id: str = Field(min_length=1, alias="documentId")
    chunk_id: str = Field(min_length=1, alias="chunkId")
    content_hash: str = Field(min_length=1, alias="contentHash")
    structural_path: str = Field(min_length=1, alias="structuralPath")
    start_line: int = Field(ge=1, alias="startLine")
    end_line: int = Field(ge=1, alias="endLine")


@dataclass(frozen=True)
class LoadedDocument:
    source: str
    suffix: str
    text: str
    mime_type: str | None = None


__all__ = [
    "DocumentChunk",
    "IngestionBaseModel",
    "LoadedDocument",
]
