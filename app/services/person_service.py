from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.repositories.person_repo import PersonRepository
from app.utils.insightface_utils import get_face_embedding


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
            raise ValueError("Không tìm thấy khuôn mặt trong ảnh, vui lòng thử ảnh khác.")

        success = await self.person_repository.update_face_embedding(person_id, embedding)
        if not success:
            raise ValueError("Không tìm thấy thông tin người trong hệ thống.")

        return True

    async def check_in_by_face(self, image_np: np.ndarray) -> CheckInMatch:
        embedding = get_face_embedding(image_np)
        if embedding is None:
            return CheckInMatch(
                person_id=None,
                confidence=0.0,
                error="Không tìm thấy khuôn mặt trong ảnh.",
            )

        person, confidence = await self.person_repository.find_nearest_person_by_embedding(
            embedding,
            settings.FACE_MATCH_THRESHOLD,
        )
        if person is None:
            return CheckInMatch(
                person_id=None,
                confidence=confidence,
                error="Không tìm thấy người phù hợp với ngưỡng nhận diện.",
            )

        return CheckInMatch(person_id=person.person_id, confidence=confidence)

    async def remove_person_face_embedding(
        self, person_id: UUID
    ) -> Literal["deleted", "already_empty", "not_found"]:
        return await self.person_repository.remove_face_embedding(person_id)
