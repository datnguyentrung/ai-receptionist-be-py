import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ontology_schema_proposal import (
    OntologySchemaProposal,
    SchemaProposalStatus,
    SchemaProposalType,
)


class OntologySchemaProposalService:
    """Service thao tác cơ sở dữ liệu cho bảng OntologySchemaProposal."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(self, proposal_id: uuid.UUID) -> OntologySchemaProposal | None:
        """Lấy thông tin đề xuất thay đổi Schema theo ID."""
        result = await self.db.execute(
            select(OntologySchemaProposal).where(
                OntologySchemaProposal.id == proposal_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_version(
        self,
        ontology_version_id: uuid.UUID,
        status: SchemaProposalStatus | None = None,
        proposal_type: SchemaProposalType | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[OntologySchemaProposal]:
        """Lấy danh sách các đề xuất thay đổi Schema theo phiên bản (có lọc status / type)."""
        stmt = select(OntologySchemaProposal).where(
            OntologySchemaProposal.ontology_version_id == ontology_version_id
        )

        if status:
            stmt = stmt.where(OntologySchemaProposal.status == status)
        if proposal_type:
            stmt = stmt.where(OntologySchemaProposal.proposal_type == proposal_type)

        stmt = stmt.order_by(OntologySchemaProposal.created_at.desc()).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def create(self, obj_in: dict[str, Any]) -> OntologySchemaProposal:
        """Tạo mới một đề xuất thay đổi Schema (do LLM/Human đưa ra)."""
        db_obj = OntologySchemaProposal(**obj_in)
        self.db.add(db_obj)
        await self.db.flush()
        await self.db.refresh(db_obj)
        return db_obj

    async def update_status(
        self,
        proposal_id: uuid.UUID,
        status: SchemaProposalStatus,
        reviewed_by: str | None = None,
        reviewed_at: Any = None,
    ) -> OntologySchemaProposal | None:
        """Cập nhật trạng thái duyệt/từ chối/áp dụng của đề xuất Schema."""
        db_obj = await self.get_by_id(proposal_id)
        if not db_obj:
            return None

        db_obj.status = status
        if reviewed_by:
            db_obj.reviewed_by = reviewed_by
        if reviewed_at:
            db_obj.reviewed_at = reviewed_at

        self.db.add(db_obj)
        await self.db.flush()
        await self.db.refresh(db_obj)
        return db_obj

    async def update(
        self, db_obj: OntologySchemaProposal, obj_in: dict[str, Any]
    ) -> OntologySchemaProposal:
        """Cập nhật chi tiết nội dung đề xuất Schema."""
        for field, value in obj_in.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)
        self.db.add(db_obj)
        await self.db.flush()
        await self.db.refresh(db_obj)
        return db_obj

    async def delete(self, proposal_id: uuid.UUID) -> bool:
        """Xóa đề xuất Schema theo ID."""
        db_obj = await self.get_by_id(proposal_id)
        if not db_obj:
            return False
        await self.db.delete(db_obj)
        await self.db.flush()
        return True
