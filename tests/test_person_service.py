import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.exceptions.face_check_in_exception import (
    FaceCheckInErrorCode,
    FaceCheckInException,
)
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

        with patch(
            "app.services.person_service.get_single_face_embedding",
            return_value=SimpleNamespace(embedding=[0.1], face_count=1),
        ):
            result = await service.check_in_by_face(object())

        self.assertTrue(result.matched)
        self.assertEqual(result.person_id, person_id)
        self.assertEqual(result.confidence, 0.86)
        self.assertIsNone(result.error)

    async def test_check_in_returns_unmatched_when_below_threshold(self):
        service = PersonService.__new__(PersonService)
        service.person_repository = FakePersonRepository(person=None, confidence=0.62)

        with patch(
            "app.services.person_service.get_single_face_embedding",
            return_value=SimpleNamespace(embedding=[0.1], face_count=1),
        ):
            with self.assertRaises(FaceCheckInException) as exc:
                await service.check_in_by_face(object())

        self.assertEqual(exc.exception.error_code, FaceCheckInErrorCode.FACE_NOT_MATCHED)

    async def test_check_in_returns_unmatched_when_no_face_embedding(self):
        service = PersonService.__new__(PersonService)
        service.person_repository = FakePersonRepository()

        with patch(
            "app.services.person_service.get_single_face_embedding",
            return_value=SimpleNamespace(embedding=None, face_count=0),
        ):
            with self.assertRaises(FaceCheckInException) as exc:
                await service.check_in_by_face(object())

        self.assertEqual(exc.exception.error_code, FaceCheckInErrorCode.FACE_NOT_DETECTED)

    async def test_check_in_rejects_invalid_person_type(self):
        person_id = uuid4()
        service = PersonService.__new__(PersonService)
        service.person_repository = FakePersonRepository(
            person=SimpleNamespace(person_id=person_id, person_type="TEACHER"),
            confidence=0.86,
        )

        with patch(
            "app.services.person_service.get_single_face_embedding",
            return_value=SimpleNamespace(embedding=[0.1], face_count=1),
        ):
            with self.assertRaises(FaceCheckInException) as exc:
                await service.check_in_by_face(object())

        self.assertEqual(
            exc.exception.error_code,
            FaceCheckInErrorCode.FACE_CHECK_IN_PERSON_TYPE_INVALID,
        )

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

    def test_check_in_error_response_contract(self):
        self.assertEqual(
            FaceCheckInErrorCode.FACE_NOT_DETECTED.to_response_body(),
            {
                "code": "FACE_NOT_DETECTED",
                "statusCode": 422,
                "message": "Không phát hiện được khuôn mặt trong ảnh",
            },
        )


if __name__ == "__main__":
    unittest.main()
