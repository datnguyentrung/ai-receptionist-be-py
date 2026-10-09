"""Quản lý vòng đời tiến hóa của Ontology Schema và xuất bản Snapshot có con người phê duyệt.

Mô-đun này chịu trách nhiệm:
- Ghi nhận và theo dõi các đề xuất mở rộng schema (OntologySchemaProposal) từ quá trình Ingestion.
- Phê duyệt (Review) hoặc từ chối các đề xuất lược đồ bởi con người (Human-in-the-loop).
- Áp dụng (Apply) các đề xuất đã được phê duyệt để sinh ra phiên bản ontology mới (OntologyVersion mới),
  nhân bản (clone) toàn bộ thực thể, thuộc tính, mối quan hệ, scope và alias, sau đó biên dịch (compile)
  lại snapshot và làm mới cache.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
    OntologyAlias,
    OntologyAliasSource,
    OntologyEntityType,
    OntologyEntityTypeScope,
    OntologyProperty,
    OntologyPropertyDataType,
    OntologyRelationship,
    OntologySchemaProposal,
    OntologyScope,
    OntologyVersion,
    OntologyVersionStatus,
    RelationshipCardinality,
    SchemaProposalStatus,
    SchemaProposalType,
)
from app.services.ingestion.schema.ontology import OntologyCache
from app.services.ontology.ontology_compiler import OntologyCompiler


class OntologyLifecycle:
    """Quản lý các chuyển trạng thái của đề xuất schema và xuất bản các phiên bản ontology hoàn chỉnh."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        compiler: OntologyCompiler,
        cache: OntologyCache,
    ) -> None:
        """Khởi tạo OntologyLifecycle với session factory và các thành phần phụ thuộc.

        Args:
            session_factory (async_sessionmaker[AsyncSession]): Factory tạo session kết nối PostgreSQL.
            compiler (OntologyCompiler): Trình biên dịch ontology snapshot.
            cache (OntologyCache): Bộ nhớ đệm cache ontology của runtime.
        """
        self._session_factory = session_factory
        self._compiler = compiler
        self._cache = cache

    async def create_proposal(
        self,
        *,
        ingestion_id: str,
        batch_index: int,
        ontology_version_id: str,
        source_document_id: str | None,
        proposal_type: str | SchemaProposalType,
        technical_name: str | None,
        reason: str,
        payload: dict[str, Any],
        evidence: dict[str, Any],
        affected_scope_keys: list[str],
    ) -> OntologySchemaProposal:
        """Tạo mới một đề xuất mở rộng/chỉnh sửa lược đồ (schema proposal).

        Nếu một đề xuất có cùng nội dung mã băm (digest) đã tồn tại, trả về bản ghi hiện có (idempotent).

        Args:
            ingestion_id (str): Mã định danh phiên nạp tài liệu hiện tại.
            batch_index (int): Chỉ số mẻ (batch) phát hiện khoảng trống schema.
            ontology_version_id (str): ID phiên bản ontology đang áp dụng.
            source_document_id (str | None): ID tài liệu nguồn phát sinh tri thức.
            proposal_type (str | SchemaProposalType): Loại đề xuất schema.
            technical_name (str | None): Tên kỹ thuật của đối tượng cần thêm/sửa.
            reason (str): Lý do đề xuất thay đổi lược đồ.
            payload (dict[str, Any]): Cấu trúc chi tiết nội dung thay đổi.
            evidence (dict[str, Any]): Bằng chứng trích xuất từ tài liệu nguồn.
            affected_scope_keys (list[str]): Danh sách các scope ontology bị ảnh hưởng.

        Returns:
            OntologySchemaProposal: Bản ghi đề xuất schema đã được lưu vào cơ sở dữ liệu.
        """
        # 1. Chuẩn hóa và ánh xạ kiểu đề xuất (proposal_type) từ chuỗi hoặc enum
        if isinstance(proposal_type, SchemaProposalType):
            proposal_kind = proposal_type
        else:
            raw = str(proposal_type).strip().upper()
            aliases = {
                "EXTEND_RELATIONSHIP": SchemaProposalType.MODIFY_RELATIONSHIP,
                "EXTEND_RELATIONSHIP_SOURCE": SchemaProposalType.NEW_RELATIONSHIP,
                "EXTEND_RELATIONSHIP_TARGET": SchemaProposalType.NEW_RELATIONSHIP,
                "ADD_RELATIONSHIP": SchemaProposalType.NEW_RELATIONSHIP,
                "ADD_PROPERTY": SchemaProposalType.NEW_PROPERTY,
                "ADD_ENTITY_TYPE": SchemaProposalType.NEW_ENTITY_TYPE,
                "ADD_ENTITY": SchemaProposalType.NEW_ENTITY_TYPE,
                "ADD_SCOPE": SchemaProposalType.NEW_SCOPE,
            }
            proposal_kind = aliases.get(raw) or SchemaProposalType(raw)

        # 2. Đảm bảo payload và evidence là dict an toàn
        payload = dict(payload) if payload else {}
        evidence = dict(evidence) if evidence else {}
        if technical_name and "technicalName" not in payload:
            payload["technicalName"] = technical_name

        # 3. Tính toán mã băm sha256 định danh duy nhất (digest) cho nội dung đề xuất
        digest = _digest({
            "ingestionId": ingestion_id,
            "batchIndex": batch_index,
            "type": proposal_kind.value,
            "technicalName": technical_name,
            "payload": payload,
            "evidence": evidence,
        })

        # 4. Lưu bản ghi vào PostgreSQL nếu chưa tồn tại
        async with self._session_factory() as session, session.begin():
            existing = await session.scalar(
                select(OntologySchemaProposal).where(
                    OntologySchemaProposal.proposal_digest == digest
                )
            )
            if existing is not None:
                return existing

            proposal = OntologySchemaProposal(
                ontology_version_id=uuid.UUID(ontology_version_id),
                proposal_type=proposal_kind,
                status=SchemaProposalStatus.PROPOSED,
                technical_name=technical_name,
                reason=reason,
                payload=payload,
                evidence=evidence,
                source_document_id=source_document_id,
                source_ingestion_id=ingestion_id,
                source_batch_index=batch_index,
                affected_scope_keys=list(dict.fromkeys(affected_scope_keys)),
                proposal_digest=digest,
            )
            session.add(proposal)
            await session.flush()
            return proposal

    async def review_proposal(
        self, proposal_id: str, *, approved: bool, reviewed_by: str
    ) -> OntologySchemaProposal:
        """Ghi nhận phê duyệt hoặc từ chối một đề xuất lược đồ từ phía con người (Human Review).

        Args:
            proposal_id (str): Mã định danh đề xuất cần xem xét.
            approved (bool): Trạng thái phê duyệt (True: Đồng ý, False: Từ chối).
            reviewed_by (str): Tên hoặc định danh người thực hiện duyệt.

        Returns:
            OntologySchemaProposal: Bản ghi đề xuất sau khi cập nhật trạng thái.

        Raises:
            ValueError: Khi reviewed_by rỗng hoặc đề xuất không ở trạng thái PROPOSED.
            KeyError: Khi không tìm thấy đề xuất theo proposal_id.
        """
        # 1. Kiểm tra thông tin người duyệt
        if not reviewed_by.strip():
            raise ValueError("reviewed_by is required")

        # 2. Mở transaction và khóa dòng (SELECT FOR UPDATE) để đảm bảo tính nhất quán
        async with self._session_factory() as session, session.begin():
            proposal = await session.scalar(
                select(OntologySchemaProposal)
                .where(OntologySchemaProposal.id == uuid.UUID(proposal_id))
                .with_for_update()
            )
            if proposal is None:
                raise KeyError(proposal_id)
            if proposal.status != SchemaProposalStatus.PROPOSED:
                raise ValueError(f"Proposal is already {proposal.status.value}")

            # 3. Cập nhật trạng thái duyệt và thời điểm
            proposal.status = proposal_review_status(proposal.status, approved)
            proposal.reviewed_by = reviewed_by
            proposal.reviewed_at = datetime.now(UTC)
            return proposal

    async def apply_proposal(
        self, proposal_id: str, *, new_version_code: str, applied_by: str
    ) -> OntologyVersion:
        """Áp dụng đề xuất đã được phê duyệt để tạo và kích hoạt phiên bản Ontology mới.

        Quy trình:
        1. Kiểm tra đề xuất phải ở trạng thái APPROVED.
        2. Nhánh từ phiên bản ACTIVE hiện tại sang phiên bản DRAFT mới (sao chép toàn bộ thực thể, thuộc tính...).
        3. Áp dụng thay đổi từ payload của đề xuất vào phiên bản mới.
        4. Biên dịch toàn bộ các snapshot qua OntologyCompiler.
        5. Đưa phiên bản cũ về ARCHIVED và kích hoạt (ACTIVE) phiên bản mới.
        6. Làm mới OntologyCache trong bộ nhớ.

        Args:
            proposal_id (str): Mã định danh đề xuất schema cần áp dụng.
            new_version_code (str): Mã phiên bản mới cần tạo (vd: "v1.1.0").
            applied_by (str): Định danh người/hệ thống thực hiện áp dụng.

        Returns:
            OntologyVersion: Phiên bản ontology mới đã được kích hoạt.

        Raises:
            KeyError: Không tìm thấy proposal.
            ValueError: Proposal chưa được duyệt hoặc version code đã tồn tại.
            RuntimeError: Không tìm thấy phiên bản active hiện tại để kế thừa.
        """
        async with self._session_factory() as session, session.begin():
            # 1. Truy vấn đề xuất schema cần áp dụng
            proposal = await session.scalar(
                select(OntologySchemaProposal)
                .where(OntologySchemaProposal.id == uuid.UUID(proposal_id))
                .with_for_update()
            )
            if proposal is None:
                raise KeyError(proposal_id)
            if proposal.status == SchemaProposalStatus.APPLIED and proposal.applied_ontology_version_id:
                applied_version = await session.get(
                    OntologyVersion, proposal.applied_ontology_version_id
                )
                if applied_version is None:
                    raise KeyError(
                        f"Applied ontology version not found: {proposal.applied_ontology_version_id}"
                    )
                return applied_version
            if proposal.status != SchemaProposalStatus.APPROVED:
                raise ValueError("Only an APPROVED proposal can be applied")

            # 2. Kiểm tra tính duy nhất của mã phiên bản mới
            if await session.scalar(
                select(OntologyVersion).where(OntologyVersion.version == new_version_code)
            ):
                raise ValueError(f"Ontology version already exists: {new_version_code}")

            # 3. Khóa phiên bản đang ACTIVE để làm gốc kế thừa
            active = await session.scalar(
                select(OntologyVersion)
                .where(OntologyVersion.status == OntologyVersionStatus.ACTIVE)
                .with_for_update()
            )
            if active is None:
                raise RuntimeError("No active ontology version")

            # 4. Sao chép toàn bộ cấu trúc ontology hiện tại sang phiên bản mới
            target, maps = await self._clone_version(
                session, active.id, new_version_code, applied_by
            )

            # 5. Áp dụng các thay đổi từ proposal vào phiên bản mới
            await self._apply_change(session, proposal, target.id, maps)

            # 6. Biên dịch lại toàn bộ snapshots của phiên bản mới
            await self._compiler.compile_all(session, target.id)

            # 7. Lưu trữ phiên bản cũ (ARCHIVED) và kích hoạt phiên bản mới (ACTIVE)
            active.status = OntologyVersionStatus.ARCHIVED
            await session.flush()

            target.status = OntologyVersionStatus.ACTIVE
            target.activated_at = datetime.now(UTC)
            proposal.status = SchemaProposalStatus.APPLIED
            proposal.applied_ontology_version_id = target.id
            await session.flush()

        # 8. Làm mới bộ nhớ đệm cache
        await self._cache.refresh()
        return target

    async def get_proposal(self, proposal_id: str) -> OntologySchemaProposal | None:
        """Tra cứu đề xuất schema theo ID từ PostgreSQL.

        Args:
            proposal_id (str): Mã UUID của đề xuất cần tìm.

        Returns:
            OntologySchemaProposal | None: Đối tượng đề xuất hoặc None nếu không tồn tại.
        """
        async with self._session_factory() as session:
            return await session.get(OntologySchemaProposal, uuid.UUID(proposal_id))

    async def _clone_version(
        self, session: AsyncSession, source_id: uuid.UUID, version_code: str, created_by: str
    ) -> tuple[OntologyVersion, dict[str, dict[uuid.UUID, Any]]]:
        """Sao chép toàn bộ thực thể, thuộc tính, quan hệ, scope và alias sang phiên bản mới.

        Args:
            session (AsyncSession): Session cơ sở dữ liệu hiện tại.
            source_id (uuid.UUID): ID phiên bản nguồn cần sao chép.
            version_code (str): Mã chuỗi phiên bản mới (vd: "v1.1.0").
            created_by (str): Định danh người tạo.

        Returns:
            tuple[OntologyVersion, dict[str, dict[uuid.UUID, Any]]]:
                Phiên bản mới và bản đồ ánh xạ ID từ phiên bản cũ sang thực thể mới.
        """
        # 1. Tạo bản ghi OntologyVersion mới ở trạng thái DRAFT
        source = await session.get(OntologyVersion, source_id)
        if source is None:
            raise KeyError(source_id)
        target = OntologyVersion(
            version=version_code,
            status=OntologyVersionStatus.DRAFT,
            parent_version_id=source.id,
            description=f"Derived from {source.version}",
            created_by=created_by,
        )
        session.add(target)
        await session.flush()

        # 2. Nhân bản tất cả Entity Types
        entity_map: dict[uuid.UUID, OntologyEntityType] = {}
        for item in (
            await session.scalars(
                select(OntologyEntityType).where(
                    OntologyEntityType.ontology_version_id == source_id
                )
            )
        ).all():
            clone = OntologyEntityType(
                ontology_version_id=target.id,
                technical_name=item.technical_name,
                display_name=item.display_name,
                description=item.description,
                identity_strategy=item.identity_strategy,
                metadata_=item.metadata_,
            )
            session.add(clone)
            entity_map[item.id] = clone
        await session.flush()

        # 3. Nhân bản tất cả Properties
        property_map: dict[uuid.UUID, OntologyProperty] = {}
        for item in (
            await session.scalars(
                select(OntologyProperty).where(
                    OntologyProperty.ontology_version_id == source_id
                )
            )
        ).all():
            clone = OntologyProperty(
                ontology_version_id=target.id,
                entity_type_id=entity_map[item.entity_type_id].id,
                technical_name=item.technical_name,
                display_name=item.display_name,
                data_type=item.data_type,
                required=item.required,
                multi_value=item.multi_value,
                constraints=item.constraints,
                description=item.description,
            )
            session.add(clone)
            property_map[item.id] = clone

        # 4. Nhân bản tất cả Relationships
        relationship_map: dict[uuid.UUID, OntologyRelationship] = {}
        for item in (
            await session.scalars(
                select(OntologyRelationship).where(
                    OntologyRelationship.ontology_version_id == source_id
                )
            )
        ).all():
            clone = OntologyRelationship(
                ontology_version_id=target.id,
                technical_name=item.technical_name,
                display_name=item.display_name,
                source_entity_type_id=entity_map[item.source_entity_type_id].id,
                target_entity_type_id=entity_map[item.target_entity_type_id].id,
                cardinality=item.cardinality,
                description=item.description,
                constraints=item.constraints,
            )
            session.add(clone)
            relationship_map[item.id] = clone
        await session.flush()

        # 5. Nhân bản tất cả Scopes
        scope_map: dict[uuid.UUID, OntologyScope] = {}
        scopes = list(
            (
                await session.scalars(
                    select(OntologyScope).where(
                        OntologyScope.ontology_version_id == source_id
                    )
                )
            ).all()
        )
        for item in scopes:
            clone = OntologyScope(
                ontology_version_id=target.id,
                scope_key=item.scope_key,
                description=item.description,
                summary=item.summary or {},
            )
            session.add(clone)
            scope_map[item.id] = clone
        await session.flush()

        # 6. Nhân bản liên kết giữa Entity Type và Scope
        memberships = (
            list(
                (
                    await session.scalars(
                        select(OntologyEntityTypeScope).where(
                            OntologyEntityTypeScope.scope_id.in_([item.id for item in scopes])
                        )
                    )
                ).all()
            )
            if scopes
            else []
        )
        session.add_all([
            OntologyEntityTypeScope(
                ontology_version_id=target.id,
                scope_id=scope_map[item.scope_id].id,
                entity_type_id=entity_map[item.entity_type_id].id,
            )
            for item in memberships
        ])

        # 7. Nhân bản tất cả Aliases
        for item in (
            await session.scalars(
                select(OntologyAlias).where(
                    OntologyAlias.ontology_version_id == source_id
                )
            )
        ).all():
            session.add(
                OntologyAlias(
                    ontology_version_id=target.id,
                    alias=item.alias,
                    entity_type_id=entity_map[item.entity_type_id].id if item.entity_type_id else None,
                    property_id=property_map[item.property_id].id if item.property_id else None,
                    relationship_id=relationship_map[item.relationship_id].id if item.relationship_id else None,
                    confidence=item.confidence,
                    source=item.source,
                )
            )
        await session.flush()

        return target, {
            "entities": entity_map,
            "properties": property_map,
            "relationships": relationship_map,
            "scopes": scope_map,
        }

    async def _apply_change(
        self,
        session: AsyncSession,
        proposal: OntologySchemaProposal,
        version_id: uuid.UUID,
        maps: dict[str, dict[uuid.UUID, Any]],
    ) -> None:
        """Áp dụng chi tiết nội dung thay đổi của đề xuất vào phiên bản ontology mới.

        Args:
            session (AsyncSession): Session cơ sở dữ liệu hiện tại.
            proposal (OntologySchemaProposal): Đề xuất đã được duyệt.
            version_id (uuid.UUID): ID phiên bản ontology mới.
            maps (dict[str, dict[uuid.UUID, Any]]): Bản đồ ánh xạ thực thể đã clone.
        """
        del maps  # Maps dự phòng cho việc đối chiếu nâng cao
        payload = proposal.payload
        kind = proposal.proposal_type

        # Trường hợp 1: Thêm Scope mới
        if kind == SchemaProposalType.NEW_SCOPE:
            scope = OntologyScope(
                ontology_version_id=version_id,
                scope_key=payload["scopeKey"].strip().casefold(),
                description=payload["description"],
                summary=payload.get("summary", {}),
            )
            session.add(scope)
            await session.flush()
            entities = await self._entities_by_names(
                session, version_id, payload.get("entityTypeTechnicalNames", [])
            )
            session.add_all([
                OntologyEntityTypeScope(
                    ontology_version_id=version_id,
                    scope_id=scope.id,
                    entity_type_id=item.id,
                )
                for item in entities
            ])
            return

        # Trường hợp 2: Chỉnh sửa Scope hiện có
        if kind == SchemaProposalType.MODIFY_SCOPE:
            scope = await self._scope_by_key(session, version_id, payload["scopeKey"])
            if "description" in payload:
                scope.description = payload["description"]
            entities = await self._entities_by_names(
                session, version_id, payload.get("addEntityTypeTechnicalNames", [])
            )
            for item in entities:
                exists = await session.get(OntologyEntityTypeScope, (scope.id, item.id))
                if not exists:
                    session.add(
                        OntologyEntityTypeScope(
                            ontology_version_id=version_id,
                            scope_id=scope.id,
                            entity_type_id=item.id,
                        )
                    )
            removed = await self._entities_by_names(
                session, version_id, payload.get("removeEntityTypeTechnicalNames", [])
            )
            if removed:
                await session.execute(
                    delete(OntologyEntityTypeScope).where(
                        OntologyEntityTypeScope.scope_id == scope.id,
                        OntologyEntityTypeScope.entity_type_id.in_([item.id for item in removed]),
                    )
                )
            return

        # Trường hợp 3: Thêm Entity Type mới
        if kind == SchemaProposalType.NEW_ENTITY_TYPE:
            entity = OntologyEntityType(
                ontology_version_id=version_id,
                technical_name=payload["technicalName"],
                display_name=payload.get("displayName", payload["technicalName"]),
                description=payload.get("description"),
                identity_strategy=payload.get("identityStrategy", {}),
                metadata_={},
            )
            session.add(entity)
            await session.flush()
            for key in payload.get("scopeKeys", proposal.affected_scope_keys):
                scope = await self._scope_by_key(session, version_id, key)
                session.add(
                    OntologyEntityTypeScope(
                        ontology_version_id=version_id,
                        scope_id=scope.id,
                        entity_type_id=entity.id,
                    )
                )
            return

        # Trường hợp 4: Thêm Property mới cho Entity Type
        if kind == SchemaProposalType.NEW_PROPERTY:
            entity = (
                await self._entities_by_names(session, version_id, [payload["entityType"]])
            )[0]
            session.add(
                OntologyProperty(
                    ontology_version_id=version_id,
                    entity_type_id=entity.id,
                    technical_name=payload["technicalName"],
                    display_name=payload.get("displayName", payload["technicalName"]),
                    description=payload.get("description"),
                    data_type=OntologyPropertyDataType(payload["dataType"]),
                    required=payload.get("required", False),
                    multi_value=payload.get("multiValue", False),
                    constraints=payload.get("constraints", {}),
                )
            )
            return

        # Trường hợp 5: Thêm Relationship mới giữa 2 Entity Types
        if kind == SchemaProposalType.NEW_RELATIONSHIP:
            source, target = await self._entities_by_names(
                session, version_id, [payload["sourceEntityType"], payload["targetEntityType"]]
            )
            session.add(
                OntologyRelationship(
                    ontology_version_id=version_id,
                    technical_name=payload["technicalName"],
                    display_name=payload.get("displayName", payload["technicalName"]),
                    description=payload.get("description"),
                    source_entity_type_id=source.id,
                    target_entity_type_id=target.id,
                    cardinality=RelationshipCardinality(payload.get("cardinality", "MANY_TO_MANY")),
                    constraints=payload.get("constraints", {}),
                )
            )
            return

        # Trường hợp 6: Thêm Alias mới
        if kind == SchemaProposalType.NEW_ALIAS:
            target_type = payload["targetType"]
            kwargs: dict[str, Any] = {}
            if target_type == "ENTITY_TYPE":
                kwargs["entity_type_id"] = (
                    await self._entities_by_names(session, version_id, [payload["targetTechnicalName"]])
                )[0].id
            elif target_type == "PROPERTY":
                entity = (
                    await self._entities_by_names(session, version_id, [payload["entityType"]])
                )[0]
                prop = await session.scalar(
                    select(OntologyProperty).where(
                        OntologyProperty.ontology_version_id == version_id,
                        OntologyProperty.entity_type_id == entity.id,
                        OntologyProperty.technical_name == payload["targetTechnicalName"],
                    )
                )
                if prop is None:
                    raise KeyError(payload["targetTechnicalName"])
                kwargs["property_id"] = prop.id
            elif target_type == "RELATIONSHIP":
                relationship = await session.scalar(
                    select(OntologyRelationship).where(
                        OntologyRelationship.ontology_version_id == version_id,
                        OntologyRelationship.technical_name == payload["targetTechnicalName"],
                    )
                )
                if relationship is None:
                    raise KeyError(payload["targetTechnicalName"])
                kwargs["relationship_id"] = relationship.id
            else:
                raise ValueError(f"Unsupported alias targetType: {target_type}")

            session.add(
                OntologyAlias(
                    ontology_version_id=version_id,
                    alias=payload["alias"],
                    confidence=payload.get("confidence", 1.0),
                    source=OntologyAliasSource.INGESTION,
                    **kwargs,
                )
            )
            return

        # Trường hợp 7: Chỉnh sửa Entity Type
        if kind == SchemaProposalType.MODIFY_ENTITY_TYPE:
            entity = (
                await self._entities_by_names(session, version_id, [payload["technicalName"]])
            )[0]
            for field in ("displayName", "description", "identityStrategy"):
                if field in payload:
                    setattr(
                        entity,
                        {
                            "displayName": "display_name",
                            "description": "description",
                            "identityStrategy": "identity_strategy",
                        }[field],
                        payload[field],
                    )
            return

        # Trường hợp 8: Chỉnh sửa Property
        if kind == SchemaProposalType.MODIFY_PROPERTY:
            entity = (
                await self._entities_by_names(session, version_id, [payload["entityType"]])
            )[0]
            prop = await session.scalar(
                select(OntologyProperty).where(
                    OntologyProperty.ontology_version_id == version_id,
                    OntologyProperty.entity_type_id == entity.id,
                    OntologyProperty.technical_name == payload["technicalName"],
                )
            )
            if prop is None:
                raise KeyError(payload["technicalName"])
            mapping = {
                "displayName": "display_name",
                "description": "description",
                "required": "required",
                "multiValue": "multi_value",
                "constraints": "constraints",
            }
            for source_field, target_field in mapping.items():
                if source_field in payload:
                    setattr(prop, target_field, payload[source_field])
            if "dataType" in payload:
                prop.data_type = OntologyPropertyDataType(payload["dataType"])
            return

        # Trường hợp 9: Chỉnh sửa Relationship
        if kind == SchemaProposalType.MODIFY_RELATIONSHIP:
            relationship = await session.scalar(
                select(OntologyRelationship).where(
                    OntologyRelationship.ontology_version_id == version_id,
                    OntologyRelationship.technical_name == payload["technicalName"],
                )
            )
            if relationship is None:
                raise KeyError(payload["technicalName"])
            mapping = {
                "displayName": "display_name",
                "description": "description",
                "constraints": "constraints",
            }
            for source_field, target_field in mapping.items():
                if source_field in payload:
                    setattr(relationship, target_field, payload[source_field])
            if "cardinality" in payload:
                relationship.cardinality = RelationshipCardinality(payload["cardinality"])
            return

        raise ValueError(f"Proposal type is not implemented safely: {kind.value}")

    @staticmethod
    async def _entities_by_names(
        session: AsyncSession, version_id: uuid.UUID, names: list[str]
    ) -> list[OntologyEntityType]:
        """Truy vấn các thực thể OntologyEntityType theo danh sách technical_name."""
        if not names:
            return []
        rows = list(
            (
                await session.scalars(
                    select(OntologyEntityType).where(
                        OntologyEntityType.ontology_version_id == version_id,
                        OntologyEntityType.technical_name.in_(names),
                    )
                )
            ).all()
        )
        by_name = {item.technical_name: item for item in rows}
        missing = [name for name in names if name not in by_name]
        if missing:
            raise KeyError(f"Unknown entity types: {missing}")
        return [by_name[name] for name in names]

    @staticmethod
    async def _scope_by_key(
        session: AsyncSession, version_id: uuid.UUID, key: str
    ) -> OntologyScope:
        """Truy vấn một scope OntologyScope theo scope_key."""
        scope = await session.scalar(
            select(OntologyScope).where(
                OntologyScope.ontology_version_id == version_id,
                OntologyScope.scope_key == key.strip().casefold(),
            )
        )
        if scope is None:
            raise KeyError(f"Unknown ontology scope: {key}")
        return scope


def _digest(value: Any) -> str:
    """Tạo chuỗi băm SHA256 an toàn từ một cấu trúc dữ liệu bất kỳ (deterministic JSON digest)."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def proposal_review_status(
    current: SchemaProposalStatus, approved: bool
) -> SchemaProposalStatus:
    """Xác định trạng thái tiếp theo sau khi người dùng phê duyệt đề xuất schema."""
    if current != SchemaProposalStatus.PROPOSED:
        raise ValueError(f"Proposal is already {current.value}")
    return SchemaProposalStatus.APPROVED if approved else SchemaProposalStatus.REJECTED
