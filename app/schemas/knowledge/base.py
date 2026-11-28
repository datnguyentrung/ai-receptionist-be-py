"""Base Pydantic schemas for Layer 2 Admin Forms and deterministic Fact Mapping."""

from typing import Any

from pydantic import BaseModel, Field


class BusinessRef(BaseModel):
    """Lightweight reference to a Layer 1 Business Entity in `be-java`."""

    entity_type: str = Field(min_length=1, max_length=100)
    business_id: str = Field(min_length=1, max_length=100)
    display_name: str | None = Field(default=None, max_length=255)


class KnowledgeFormBase(BaseModel):
    """Common tenant-scoped metadata for every Admin knowledge form."""

    tenant_id: str = Field(min_length=1, max_length=100)


class KnowledgeRelationshipRecord(BaseModel):
    """Deterministic edge emitted by FactMapper."""

    relationship_type: str
    source_entity_type: str
    source_key: str
    target_entity_type: str
    target_key: str
    target_business_ref: BusinessRef | None = None


class KnowledgeFactRecord(BaseModel):
    """Deterministic fact bundle ready for Neo4j persistence without LLM."""

    tenant_id: str
    scope_key: str
    entity_type: str
    entity_key: str
    properties: dict[str, Any]
    business_refs: list[BusinessRef] = Field(default_factory=list)
    relationships: list[KnowledgeRelationshipRecord] = Field(default_factory=list)
    fact_statements: list[str] = Field(default_factory=list)
