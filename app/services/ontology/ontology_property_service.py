import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ontology_property import OntologyProperty


class OntologyPropertyService:
    """Service thao tác cơ sở dữ liệu cho bảng OntologyProperty."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, property_id: uuid.UUID) -> OntologyProperty | None:
        """Lấy thông tin thuộc tính theo ID."""
        result = await self.db.execute(
            select(OntologyProperty).where(OntologyProperty.id == property_id)
        )
        return result.scalar_one_or_none()

    async def get_by_entity_type(
        self, entity_type_id: uuid.UUID
    ) -> Sequence[OntologyProperty]:
        """Lấy danh sách các thuộc tính thuộc một loại thực thể (Entity Type)."""
        result = await self.db.execute(
            select(OntologyProperty)
            .where(OntologyProperty.entity_type_id == entity_type_id)
            .order_by(OntologyProperty.created_at.asc())
        )
        return result.scalars().all()

    async def get_by_version(
        self, ontology_version_id: uuid.UUID, skip: int = 0, limit: int = 100
    ) -> Sequence[OntologyProperty]:
        """Lấy danh sách thuộc tính thuộc 1 phiên bản Ontology."""
        stmt = (
            select(OntologyProperty)
            .where(OntologyProperty.ontology_version_id == ontology_version_id)
            .order_by(OntologyProperty.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def get_by_technical_name(
        self, entity_type_id: uuid.UUID, technical_name: str
    ) -> OntologyProperty | None:
        """Lấy thuộc tính theo technical_name trong cùng 1 Entity Type."""
        result = await self.db.execute(
            select(OntologyProperty).where(
                OntologyProperty.entity_type_id == entity_type_id,
                OntologyProperty.technical_name == technical_name,
            )
        )
        return result.scalar_one_or_none()

    async def create(self, obj_in: dict[str, Any]) -> OntologyProperty:
        """Tạo mới một thuộc tính (Property)."""
        db_obj = OntologyProperty(**obj_in)
        self.db.add(db_obj)
        await self.db.flush()
        await self.db.refresh(db_obj)
        return db_obj

    async def update(
        self, db_obj: OntologyProperty, obj_in: dict[str, Any]
    ) -> OntologyProperty:
        """Cập nhật thông tin thuộc tính."""
        for field, value in obj_in.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)
        self.db.add(db_obj)
        await self.db.flush()
        await self.db.refresh(db_obj)
        return db_obj

    async def delete(self, property_id: uuid.UUID) -> bool:
        """Xóa thuộc tính theo ID."""
        db_obj = await self.get_by_id(property_id)
        if not db_obj:
            return False
        await self.db.delete(db_obj)
        await self.db.flush()
        return True
