import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ontology_version import OntologyVersion, OntologyVersionStatus


class OntologyVersionService:
    """Service thao tác cơ sở dữ liệu cho bảng OntologyVersion."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, version_id: uuid.UUID) -> OntologyVersion | None:
        """Lấy thông tin phiên bản Ontology theo ID."""
        result = await self.db.execute(
            select(OntologyVersion).where(OntologyVersion.id == version_id)
        )
        return result.scalar_one_or_none()

    async def get_by_version_code(self, version_code: str) -> OntologyVersion | None:
        """Lấy thông tin phiên bản Ontology theo mã phiên bản (vd: 'v1.0.0')."""
        result = await self.db.execute(
            select(OntologyVersion).where(OntologyVersion.version == version_code)
        )
        return result.scalar_one_or_none()

    async def get_active_version(self) -> OntologyVersion | None:
        """Lấy phiên bản Ontology đang ACTIVE hiện tại."""
        result = await self.db.execute(
            select(OntologyVersion).where(
                OntologyVersion.status == OntologyVersionStatus.ACTIVE
            )
        )
        return result.scalar_one_or_none()

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
        status: OntologyVersionStatus | None = None,
    ) -> Sequence[OntologyVersion]:
        """Lấy danh sách các phiên bản Ontology (có phân trang và lọc status)."""
        stmt = select(OntologyVersion)
        if status:
            stmt = stmt.where(OntologyVersion.status == status)
        stmt = stmt.order_by(OntologyVersion.created_at.desc()).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def create(self, obj_in: dict[str, Any]) -> OntologyVersion:
        """Tạo mới phiên bản Ontology."""
        db_obj = OntologyVersion(**obj_in)
        self.db.add(db_obj)
        await self.db.flush()
        await self.db.refresh(db_obj)
        return db_obj

    async def update(
        self, db_obj: OntologyVersion, obj_in: dict[str, Any]
    ) -> OntologyVersion:
        """Cập nhật phiên bản Ontology."""
        for field, value in obj_in.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)
        self.db.add(db_obj)
        await self.db.flush()
        await self.db.refresh(db_obj)
        return db_obj

    async def delete(self, version_id: uuid.UUID) -> bool:
        """Xóa phiên bản Ontology theo ID."""
        db_obj = await self.get_by_id(version_id)
        if not db_obj:
            return False
        await self.db.delete(db_obj)
        await self.db.flush()
        return True
