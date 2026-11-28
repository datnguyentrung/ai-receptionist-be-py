"""Pydantic form schemas for Center Profile and Facility Item (Scope: center_overview)."""

from enum import StrEnum

from pydantic import Field

from app.schemas.knowledge.base import KnowledgeFormBase


class FacilityCategory(StrEnum):
    MAT = "MAT"
    ARMOR = "ARMOR"
    ROOM_AMENITY = "ROOM_AMENITY"
    SAFETY = "SAFETY"
    EQUIPMENT = "EQUIPMENT"


class CenterProfileFormRequest(KnowledgeFormBase):
    title: str = Field(min_length=1, max_length=255)
    history: str | None = None
    vision: str | None = None
    mission: str | None = None
    email: str | None = Field(default=None, max_length=255)
    zalo_url: str | None = Field(default=None, max_length=500)
    facebook_url: str | None = Field(default=None, max_length=500)
    website_url: str | None = Field(default=None, max_length=500)
    support_hours: str | None = Field(default=None, max_length=255)
    branch_ids: list[int] = Field(default_factory=list)


class CenterProfileFormResponse(CenterProfileFormRequest):
    entity_key: str
    scope_key: str = "center_overview"


class FacilityItemFormRequest(KnowledgeFormBase):
    name: str = Field(min_length=1, max_length=255)
    category: FacilityCategory
    quantity_note: str | None = Field(default=None, max_length=255)
    description: str = Field(min_length=1)
    branch_id: int | None = None


class FacilityItemFormResponse(FacilityItemFormRequest):
    entity_key: str
    scope_key: str = "center_overview"
