import logging
from uuid import UUID

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.exceptions.face_check_in_exception import (
    FaceCheckInErrorCode,
    FaceCheckInException,
)
from app.schemas.response import CheckInResponse
from app.services.person_service import PersonService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Person"])
person_router = router


def _check_in_error_response(
    error_code: FaceCheckInErrorCode,
    detail: str | None = None,
) -> JSONResponse:
    content = error_code.to_response_body()
    if detail:
        content["detail"] = detail
    return JSONResponse(status_code=error_code.status_code, content=content)


def _decode_image(contents: bytes, request_id: str | None = None) -> np.ndarray:
    logger.info(
        "CHECK_IN_STEP before_imdecode | request_id=%s | size_bytes=%s",
        request_id,
        len(contents),
    )
    try:
        parser = np.frombuffer(contents, np.uint8)
        image_np = cv2.imdecode(parser, cv2.IMREAD_COLOR)
    except Exception:
        logger.exception("CHECK_IN_ERROR imdecode_exception | request_id=%s", request_id)
        raise

    image_shape = tuple(image_np.shape) if image_np is not None else None
    logger.info(
        "CHECK_IN_STEP after_imdecode | request_id=%s | valid=%s | image_shape=%s",
        request_id,
        image_np is not None,
        image_shape,
    )
    if image_np is None:
        raise HTTPException(
            status_code=400,
            detail=FaceCheckInErrorCode.FACE_IMAGE_INVALID.message,
        )
    return image_np


@router.post("/check-in", response_model=CheckInResponse)
async def face_check_in(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> CheckInResponse | JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.info("CHECK_IN_STEP entered_endpoint | request_id=%s", request_id)

    try:
        contents = await file.read()
    except Exception:
        logger.exception("CHECK_IN_ERROR file_read_failed | request_id=%s", request_id)
        return _check_in_error_response(FaceCheckInErrorCode.FACE_IMAGE_INVALID)

    logger.info(
        "CHECK_IN_STEP file_read_completed | request_id=%s | filename=%s | "
        "content_type=%s | size_bytes=%s",
        request_id,
        file.filename,
        file.content_type,
        len(contents),
    )
    if not contents:
        logger.error("CHECK_IN_ERROR missing_image | request_id=%s", request_id)
        return _check_in_error_response(FaceCheckInErrorCode.FACE_IMAGE_INVALID)

    try:
        image_np = _decode_image(contents, request_id=request_id)
    except HTTPException:
        logger.exception("CHECK_IN_ERROR invalid_image | request_id=%s", request_id)
        return _check_in_error_response(FaceCheckInErrorCode.FACE_IMAGE_INVALID)
    except Exception as exc:
        logger.exception("CHECK_IN_ERROR image_decode_failed | request_id=%s", request_id)
        return _check_in_error_response(
            FaceCheckInErrorCode.PYTHON_BACKEND_ERROR,
            detail=str(exc),
        )

    person_service = PersonService(db)

    try:
        match = await person_service.check_in_by_face(image_np, request_id=request_id)
    except FaceCheckInException as exc:
        logger.info(
            "CHECK_IN_ERROR business_error | request_id=%s | code=%s",
            request_id,
            exc.error_code.name,
        )
        return _check_in_error_response(exc.error_code)
    except RuntimeError as exc:
        logger.exception("CHECK_IN_ERROR backend_unavailable | request_id=%s", request_id)
        return _check_in_error_response(
            FaceCheckInErrorCode.PYTHON_BACKEND_UNAVAILABLE,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("CHECK_IN_ERROR check_in_failed | request_id=%s", request_id)
        return _check_in_error_response(
            FaceCheckInErrorCode.PYTHON_BACKEND_ERROR,
            detail=str(exc),
        )

    response = CheckInResponse(
        matched=match.matched,
        person_id=match.person_id,
        confidence=match.confidence,
        error=match.error,
    )
    logger.info(
        "CHECK_IN_STEP before_response | request_id=%s | matched=%s | "
        "confidence=%.6f | has_error=%s",
        request_id,
        response.matched,
        response.confidence,
        response.error is not None,
    )
    return response


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
async def delete_face_embedding(personId: UUID, db: AsyncSession = Depends(get_db)):
    person_service = PersonService(db)
    result = await person_service.remove_person_face_embedding(personId)

    if result == "not_found":
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy thông tin người trong hệ thống.",
        )

    if result == "already_empty":
        return {
            "status": "success",
            "message": "Người này chưa có dữ liệu khuôn mặt, không cần xóa.",
        }

    return {"status": "success", "message": "Xóa dữ liệu khuôn mặt thành công!"}
