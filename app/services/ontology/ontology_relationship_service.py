import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ontology_relationship import OntologyRelationship


class OntologyRelationshipService:
    """Service thao tác cơ sở dữ liệu cho bảng OntologyRelationship."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, relationship_id: uuid.UUID) -> OntologyRelationship | None:
        """Lấy thông tin quan hệ theo ID."""
        result = await self.db.execute(
            select(OntologyRelationship).where(OntologyRelationship.id == relationship_id)
        )
        return result.scalar_one_or_none()

    async def get_by_version(
        self, ontology_version_id: uuid.UUID, skip: int = 0, limit: int = 100
    ) -> Sequence[OntologyRelationship]:
        """Lấy tất cả mối quan hệ thuộc 1 phiên bản Ontology."""
        stmt = (
            select(OntologyRelationship)
            .where(OntologyRelationship.ontology_version_id == ontology_version_id)
            .order_by(OntologyRelationship.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_by_entity_type(
        self, entity_type_id: uuid.UUID
    ) -> Sequence[OntologyRelationship]:
        """Lấy các quan hệ mà Entity Type đóng vai trò là Source hoặc Target."""
        stmt = select(OntologyRelationship).where(
            or_(
                OntologyRelationship.source_entity_type_id == entity_type_id,
                OntologyRelationship.target_entity_type_id == entity_type_id,
            )
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def create(self, obj_in: dict[str, Any]) -> OntologyRelationship:
        """Tạo mới một quan hệ (Relationship)."""
        db_obj = OntologyRelationship(**obj_in)
        self.db.add(db_obj)
        await self.db.flush()
        await self.db.refresh(db_obj)
        return db_obj

    async def update(
        self, db_obj: OntologyRelationship, obj_in: dict[str, Any]
    ) -> OntologyRelationship:
        """Cập nhật thông tin quan hệ."""
        for field, value in obj_in.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)
        self.db.add(db_obj)
        await self.db.flush()
        await self.db.refresh(db_obj)
        return db_obj

    async def delete(self, relationship_id: uuid.UUID) -> bool:
        """Xóa quan hệ theo ID."""
        db_obj = await self.get_by_id(relationship_id)
        if not db_obj:
            return False
        await self.db.delete(db_obj)
        await self.db.flush()
        return True
