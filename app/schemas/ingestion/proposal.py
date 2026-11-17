"""Structured input contracts for ontology schema proposals."""

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from app.schemas.ingestion.base import FreeformDict, IngestionModel


class SchemaProposalRequest(IngestionModel):
    """Common fields shared by every schema proposal request."""

    technical_name: str | None = None

    def payload_dict(self) -> dict[str, Any]:
        """Serialize the proposal payload for the ontology lifecycle."""

        raise NotImplementedError


class IdentityStrategyProposalPayload(IngestionModel):
    required: list[str] = Field(default_factory=list)
    source_scoped: bool = False


class EntityPropertyProposalPayload(IngestionModel):
    technical_name: str
    data_type: Literal[
        "STRING", "INTEGER", "FLOAT", "BOOLEAN", "DATE", "DATETIME", "UUID", "JSON"
    ]
    display_name: str | None = None
    description: str | None = None
    required: bool = False
    multi_value: bool = False
    constraints: dict[str, Any] = Field(default_factory=dict)


class NewPropertyProposalPayload(IngestionModel):
    entity_type: str
    data_type: Literal[
        "STRING", "INTEGER", "FLOAT", "BOOLEAN", "DATE", "DATETIME", "UUID", "JSON"
    ]
    display_name: str | None = None
    description: str | None = None
    required: bool = False
    multi_value: bool = False
    constraints: dict[str, Any] = Field(default_factory=dict)


class NewEntityTypeProposalPayload(IngestionModel):
    identity_strategy: IdentityStrategyProposalPayload
    properties: list[EntityPropertyProposalPayload] = Field(default_factory=list)
    display_name: str | None = None
    description: str | None = None
    scope_keys: list[str] | None = None

    @model_validator(mode="after")
    def identity_properties_are_declared(self) -> "NewEntityTypeProposalPayload":
        declared = {item.technical_name for item in self.properties}
        missing = set(self.identity_strategy.required) - declared
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"identity properties must be declared: {names}")
        return self


class NewPropertyProposal(SchemaProposalRequest):
    proposal_type: Literal["NEW_PROPERTY"]
    technical_name: str
    payload: NewPropertyProposalPayload

    def payload_dict(self) -> dict[str, Any]:
        return self.payload.model_dump(by_alias=True, exclude_none=True)


class NewEntityTypeProposal(SchemaProposalRequest):
    proposal_type: Literal["NEW_ENTITY_TYPE"]
    technical_name: str
    payload: NewEntityTypeProposalPayload

    def payload_dict(self) -> dict[str, Any]:
        return self.payload.model_dump(by_alias=True, exclude_none=True)


class OtherSchemaProposal(SchemaProposalRequest):
    """Compatibility contract for proposal types not yet fully typed."""

    proposal_type: Literal[
        "NEW_RELATIONSHIP",
        "NEW_ALIAS",
        "MODIFY_ENTITY_TYPE",
        "MODIFY_PROPERTY",
        "MODIFY_RELATIONSHIP",
        "NEW_SCOPE",
        "MODIFY_SCOPE",
    ]
    payload: FreeformDict

    def payload_dict(self) -> dict[str, Any]:
        return self.payload


SchemaProposalInput = Annotated[
    NewPropertyProposal | NewEntityTypeProposal | OtherSchemaProposal,
    Field(discriminator="proposal_type"),
]


__all__ = [
    "EntityPropertyProposalPayload",
    "IdentityStrategyProposalPayload",
    "NewEntityTypeProposal",
    "NewEntityTypeProposalPayload",
    "NewPropertyProposal",
    "NewPropertyProposalPayload",
    "OtherSchemaProposal",
    "SchemaProposalInput",
    "SchemaProposalRequest",
]
