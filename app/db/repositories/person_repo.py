from typing import Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.person import Person


class PersonRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_person_by_id(self, person_id: UUID) -> Person | None:
        result = await self.session.execute(
            select(Person).where(Person.person_id == person_id)
        )
        return result.scalars().first()

    async def update_face_embedding(
        self, person_id: UUID, embedding: list[float] | None
    ) -> bool:
        query = (
            update(Person)
            .where(Person.person_id == person_id)
            .values(face_embedding=embedding)
            .returning(Person.person_id)
        )

        result = await self.session.execute(query)
        await self.session.commit()
        return result.scalar_one_or_none() is not None

    async def remove_face_embedding(
        self, person_id: UUID
    ) -> Literal["deleted", "already_empty", "not_found"]:
        person = await self.get_person_by_id(person_id)
        if person is None:
            return "not_found"

        if person.face_embedding is None:
            return "already_empty"

        await self.session.execute(
            update(Person)
            .where(Person.person_id == person_id)
            .values(face_embedding=None)
        )
        await self.session.commit()
        return "deleted"

    async def find_nearest_person_by_embedding(
        self, embedding_vector: list[float], threshold: float
    ) -> tuple[Person | None, float]:
        distance_expr = Person.face_embedding.cosine_distance(embedding_vector)
        query = (
            select(Person, distance_expr.label("distance"))
            .where(Person.face_embedding.is_not(None))
            .order_by(distance_expr)
            .limit(1)
        )

        result = await self.session.execute(query)
        row = result.first()
        if row is None:
            return None, 0.0

        person, distance = row
        confidence = max(0.0, min(1.0, 1.0 - float(distance)))
        if confidence < threshold:
            return None, confidence

        return person, confidence
