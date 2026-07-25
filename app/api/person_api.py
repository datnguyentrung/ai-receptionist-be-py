from uuid import UUID

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.response import CheckInResponse
from app.services.person_service import PersonService

router = APIRouter(tags=["Person"])
person_router = router


def _decode_image(contents: bytes) -> np.ndarray:
    parser = np.frombuffer(contents, np.uint8)
    image_np = cv2.imdecode(parser, cv2.IMREAD_COLOR)
    if image_np is None:
        raise HTTPException(
            status_code=400,
            detail="Ảnh tải lên không hợp lệ hoặc không đúng định dạng.",
        )
    return image_np


@router.post("/check-in", response_model=CheckInResponse)
async def face_check_in(
    file: UploadFile = File(...), db: AsyncSession = Depends(get_db)
) -> CheckInResponse:
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Thiếu ảnh tải lên.")

    image_np = _decode_image(contents)
    person_service = PersonService(db)

    try:
        match = await person_service.check_in_by_face(image_np)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Có lỗi từ hệ thống AI nhận diện: {exc}",
        ) from exc

    return CheckInResponse(
        matched=match.matched,
        person_id=match.person_id,
        confidence=match.confidence,
        error=match.error,
    )


@router.post("/persons/{personId}/face-embedding")
async def upload_face_image(
    personId: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Thiếu ảnh tải lên.")

    image_np = _decode_image(contents)
    person_service = PersonService(db)

    try:
        await person_service.update_person_face_embedding(personId, image_np)
        return {
            "status": "success",
            "message": "Cập nhật dữ liệu khuôn mặt thành công!",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/persons/{personId}/face-embedding")
async def delete_face_embedding(
    personId: UUID, db: AsyncSession = Depends(get_db)
):
    person_service = PersonService(db)
    result = await person_service.remove_person_face_embedding(personId)

    if result == "not_found":
        raise HTTPException(
            status_code=404, detail="Không tìm thấy thông tin người trong hệ thống."
        )

    if result == "already_empty":
        return {
            "status": "success",
            "message": "Người này chưa có dữ liệu khuôn mặt, không cần xóa.",
        }

    return {"status": "success", "message": "Xóa dữ liệu khuôn mặt thành công!"}
