import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.repositories.person_repo import PersonRepository
from app.exceptions.face_check_in_exception import (
    FaceCheckInErrorCode,
    FaceCheckInException,
)
from app.utils.insightface_utils import get_face_embedding, get_single_face_embedding

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckInMatch:
    person_id: UUID | None
    confidence: float
    error: str | None = None

    @property
    def matched(self) -> bool:
        return self.person_id is not None


class PersonService:
    def __init__(self, session: AsyncSession):
        self.person_repository = PersonRepository(session)

    async def update_person_face_embedding(
        self, person_id: UUID, image_np: np.ndarray
    ) -> bool:
        embedding = get_face_embedding(image_np)
        if embedding is None:
            raise ValueError(
                "Không tìm thấy khuôn mặt trong ảnh, vui lòng thử ảnh khác."
            )

        success = await self.person_repository.update_face_embedding(person_id, embedding)
        if not success:
            raise ValueError("Không tìm thấy thông tin người trong hệ thống.")

        return True

    async def check_in_by_face(
        self, image_np: np.ndarray, request_id: str | None = None
    ) -> CheckInMatch:
        logger.info("CHECK_IN_STEP before_insightface | request_id=%s", request_id)
        try:
            face_result = get_single_face_embedding(image_np, request_id=request_id)
        except Exception:
            logger.exception(
                "CHECK_IN_ERROR insightface_failed | request_id=%s", request_id
            )
            raise

        logger.info(
            "CHECK_IN_STEP after_insightface | request_id=%s | embedding_present=%s",
            request_id,
            face_result.embedding is not None,
        )
        if face_result.embedding is None:
            raise FaceCheckInException(FaceCheckInErrorCode.FACE_NOT_DETECTED)

        logger.info(
            "CHECK_IN_STEP before_database_query | request_id=%s | threshold=%.6f",
            request_id,
            settings.FACE_MATCH_THRESHOLD,
        )
        try:
            person, confidence = (
                await self.person_repository.find_nearest_person_by_embedding(
                    face_result.embedding,
                    settings.FACE_MATCH_THRESHOLD,
                )
            )
        except Exception:
            logger.exception(
                "CHECK_IN_ERROR database_query_failed | request_id=%s", request_id
            )
            raise

        logger.info(
            "CHECK_IN_STEP after_database_query | request_id=%s | found=%s | "
            "confidence=%.6f",
            request_id,
            person is not None,
            confidence,
        )
        if person is None:
            raise FaceCheckInException(FaceCheckInErrorCode.FACE_NOT_MATCHED)

        person_type = getattr(person, "person_type", None)
        if person_type is not None and str(person_type).upper() not in {
            "STUDENT",
            "STUDENT_MEMBER",
        }:
            raise FaceCheckInException(
                FaceCheckInErrorCode.FACE_CHECK_IN_PERSON_TYPE_INVALID
            )

        return CheckInMatch(person_id=person.person_id, confidence=confidence)

    async def remove_person_face_embedding(
        self, person_id: UUID
    ) -> Literal["deleted", "already_empty", "not_found"]:
        return await self.person_repository.remove_face_embedding(person_id)
