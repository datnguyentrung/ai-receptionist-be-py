import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ontology_compiled_snapshot import OntologyCompiledSnapshot


class OntologyCompiledSnapshotService:
    """Service thao tác cơ sở dữ liệu cho bảng OntologyCompiledSnapshot."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_id(
        self, snapshot_id: uuid.UUID
    ) -> OntologyCompiledSnapshot | None:
        """Lấy bản Snapshot theo ID."""
        result = await self.db.execute(
            select(OntologyCompiledSnapshot).where(
                OntologyCompiledSnapshot.id == snapshot_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_version_and_scope(
        self, ontology_version_id: uuid.UUID, scope_key: str
    ) -> OntologyCompiledSnapshot | None:
        """Lấy Snapshot đã biên dịch theo phiên bản Ontology và scope_key."""
        result = await self.db.execute(
            select(OntologyCompiledSnapshot).where(
                OntologyCompiledSnapshot.ontology_version_id == ontology_version_id,
                OntologyCompiledSnapshot.scope_key == scope_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_version(
        self, ontology_version_id: uuid.UUID
    ) -> Sequence[OntologyCompiledSnapshot]:
        """Lấy danh sách Snapshot thuộc một phiên bản Ontology."""
        result = await self.db.execute(
            select(OntologyCompiledSnapshot).where(
                OntologyCompiledSnapshot.ontology_version_id == ontology_version_id
            )
        )
        return result.scalars().all()

    async def create_or_update(
        self,
        ontology_version_id: uuid.UUID,
        scope_key: str,
        schema_hash: str,
        compiled_schema: dict[str, Any],
        expires_at: Any = None,
    ) -> OntologyCompiledSnapshot:
        """Tạo mới hoặc cập nhật (upsert) bản Snapshot biên dịch cho LLM Prompt / Engine."""
        snapshot = await self.get_by_version_and_scope(ontology_version_id, scope_key)
        if snapshot:
            snapshot.schema_hash = schema_hash
            snapshot.compiled_schema = compiled_schema
            snapshot.expires_at = expires_at
        else:
            snapshot = OntologyCompiledSnapshot(
                ontology_version_id=ontology_version_id,
                scope_key=scope_key,
                schema_hash=schema_hash,
                compiled_schema=compiled_schema,
                expires_at=expires_at,
            )
            self.db.add(snapshot)

        await self.db.flush()
        await self.db.refresh(snapshot)
        return snapshot

    async def delete(self, snapshot_id: uuid.UUID) -> bool:
        """Xóa Snapshot theo ID."""
        db_obj = await self.get_by_id(snapshot_id)
        if not db_obj:
            return False
        await self.db.delete(db_obj)
        await self.db.flush()
        return True
