import uuid
from collections.abc import Sequence

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

    # Snapshot writes intentionally live only in OntologySnapshotCompiler.  Keeping
    # this service read-only prevents callers from publishing a schema that did not
    # pass the canonical compiler and hashing path.
