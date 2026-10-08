import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ontology_alias import OntologyAlias


class OntologyAliasService:
    """Service thao tác cơ sở dữ liệu cho bảng OntologyAlias."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, alias_id: uuid.UUID) -> OntologyAlias | None:
        """Lấy thông tin Alias theo ID."""
        result = await self.db.execute(
            select(OntologyAlias).where(OntologyAlias.id == alias_id)
        )
        return result.scalar_one_or_none()

    async def get_by_version(
        self, ontology_version_id: uuid.UUID, skip: int = 0, limit: int = 100
    ) -> Sequence[OntologyAlias]:
        """Lấy tất cả Alias thuộc một phiên bản Ontology."""
        stmt = (
            select(OntologyAlias)
            .where(OntologyAlias.ontology_version_id == ontology_version_id)
            .order_by(OntologyAlias.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_by_alias_string(
        self, ontology_version_id: uuid.UUID, alias_str: str
    ) -> OntologyAlias | None:
        """Tìm kiếm Alias theo chuỗi văn bản bí danh trong 1 phiên bản."""
        result = await self.db.execute(
            select(OntologyAlias).where(
                OntologyAlias.ontology_version_id == ontology_version_id,
                OntologyAlias.alias == alias_str,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_target(
        self,
        entity_type_id: uuid.UUID | None = None,
        property_id: uuid.UUID | None = None,
        relationship_id: uuid.UUID | None = None,
    ) -> Sequence[OntologyAlias]:
        """Lấy danh sách Alias gán cho một Entity Type, Property, hoặc Relationship cụ thể."""
        stmt = select(OntologyAlias)
        if entity_type_id:
            stmt = stmt.where(OntologyAlias.entity_type_id == entity_type_id)
        elif property_id:
            stmt = stmt.where(OntologyAlias.property_id == property_id)
        elif relationship_id:
            stmt = stmt.where(OntologyAlias.relationship_id == relationship_id)

        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def create(self, obj_in: dict[str, Any]) -> OntologyAlias:
        """Tạo mới một Alias."""
        db_obj = OntologyAlias(**obj_in)
        self.db.add(db_obj)
        await self.db.flush()
        await self.db.refresh(db_obj)
        return db_obj

    async def update(
        self, db_obj: OntologyAlias, obj_in: dict[str, Any]
    ) -> OntologyAlias:
        """Cập nhật thông tin Alias."""
        for field, value in obj_in.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)
        self.db.add(db_obj)
        await self.db.flush()
        await self.db.refresh(db_obj)
        return db_obj

    async def delete(self, alias_id: uuid.UUID) -> bool:
        """Xóa Alias theo ID."""
        db_obj = await self.get_by_id(alias_id)
        if not db_obj:
            return False
        await self.db.delete(db_obj)
        await self.db.flush()
        return True
