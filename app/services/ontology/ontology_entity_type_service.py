import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ontology_entity_type import OntologyEntityType


class OntologyEntityTypeService:
    """Service thao tác cơ sở dữ liệu cho bảng OntologyEntityType."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, entity_type_id: uuid.UUID) -> OntologyEntityType | None:
        """Lấy thông tin Entity Type theo ID."""
        result = await self.db.execute(
            select(OntologyEntityType).where(OntologyEntityType.id == entity_type_id)
        )
        return result.scalar_one_or_none()

    async def get_by_technical_name(
        self, ontology_version_id: uuid.UUID, technical_name: str
    ) -> OntologyEntityType | None:
        """Lấy Entity Type theo technical_name trong cùng 1 phiên bản."""
        result = await self.db.execute(
            select(OntologyEntityType).where(
                OntologyEntityType.ontology_version_id == ontology_version_id,
                OntologyEntityType.technical_name == technical_name,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_version(
        self, ontology_version_id: uuid.UUID, skip: int = 0, limit: int = 100
    ) -> Sequence[OntologyEntityType]:
        """Lấy danh sách các Entity Type thuộc 1 phiên bản Ontology."""
        stmt = (
            select(OntologyEntityType)
            .where(OntologyEntityType.ontology_version_id == ontology_version_id)
            .order_by(OntologyEntityType.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def create(self, obj_in: dict[str, Any]) -> OntologyEntityType:
        """Tạo mới một Entity Type."""
        db_obj = OntologyEntityType(**obj_in)
        self.db.add(db_obj)
        await self.db.flush()
        await self.db.refresh(db_obj)
        return db_obj

    async def update(
        self, db_obj: OntologyEntityType, obj_in: dict[str, Any]
    ) -> OntologyEntityType:
        """Cập nhật thông tin Entity Type."""
        for field, value in obj_in.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)
        self.db.add(db_obj)
        await self.db.flush()
        await self.db.refresh(db_obj)
        return db_obj

    async def delete(self, entity_type_id: uuid.UUID) -> bool:
        """Xóa Entity Type theo ID."""
        db_obj = await self.get_by_id(entity_type_id)
        if not db_obj:
            return False
        await self.db.delete(db_obj)
        await self.db.flush()
        return True
