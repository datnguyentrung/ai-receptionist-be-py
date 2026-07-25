import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.schemas.response import CheckInResponse
from app.services.person_service import PersonService


class FakePersonRepository:
    def __init__(self, person=None, confidence=0.0):
        self.person = person
        self.confidence = confidence

    async def find_nearest_person_by_embedding(self, embedding_vector, threshold):
        return self.person, self.confidence


class TestPersonService(unittest.IsolatedAsyncioTestCase):
    async def test_check_in_returns_matched_person(self):
        person_id = uuid4()
        service = PersonService.__new__(PersonService)
        service.person_repository = FakePersonRepository(
            person=SimpleNamespace(person_id=person_id),
            confidence=0.86,
        )

        with patch("app.services.person_service.get_face_embedding", return_value=[0.1]):
            result = await service.check_in_by_face(object())

        self.assertTrue(result.matched)
        self.assertEqual(result.person_id, person_id)
        self.assertEqual(result.confidence, 0.86)
        self.assertIsNone(result.error)

    async def test_check_in_returns_unmatched_when_below_threshold(self):
        service = PersonService.__new__(PersonService)
        service.person_repository = FakePersonRepository(person=None, confidence=0.62)

        with patch("app.services.person_service.get_face_embedding", return_value=[0.1]):
            result = await service.check_in_by_face(object())

        self.assertFalse(result.matched)
        self.assertIsNone(result.person_id)
        self.assertEqual(result.confidence, 0.62)
        self.assertIsNotNone(result.error)

    async def test_check_in_returns_unmatched_when_no_face_embedding(self):
        service = PersonService.__new__(PersonService)
        service.person_repository = FakePersonRepository()

        with patch("app.services.person_service.get_face_embedding", return_value=None):
            result = await service.check_in_by_face(object())

        self.assertFalse(result.matched)
        self.assertIsNone(result.person_id)
        self.assertEqual(result.confidence, 0.0)
        self.assertIsNotNone(result.error)

    def test_check_in_response_uses_camel_case_alias(self):
        person_id = uuid4()
        response = CheckInResponse(
            matched=True,
            person_id=person_id,
            confidence=0.86,
        )

        self.assertEqual(
            response.model_dump(by_alias=True),
            {
                "matched": True,
                "personId": person_id,
                "confidence": 0.86,
                "error": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
