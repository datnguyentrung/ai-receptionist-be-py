import logging

from fastapi import Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.enums.face_embedding_error_code import FaceEmbeddingErrorCode
from app.exceptions.face_embedding_exception import FaceEmbeddingException
from app.schemas.face_embedding_schema import FaceEmbeddingResponse

logger = logging.getLogger(__name__)


def build_face_embedding_error_response(
    error_code: FaceEmbeddingErrorCode,
    message: str | None = None,
) -> JSONResponse:
    body = FaceEmbeddingResponse(
        success=False,
        embedding=None,
        dimension=None,
        model=settings.INSIGHTFACE_MODEL_NAME,
        errorCode=error_code,
        message=message or error_code.message,
    )
    return JSONResponse(
        status_code=error_code.status_code,
        content=body.model_dump(mode="json"),
    )


async def face_embedding_exception_handler(
    request: Request,
    exc: FaceEmbeddingException,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.info(
        "request_failed | requestId=%s | errorCode=%s",
        request_id,
        exc.error_code.value,
    )
    return build_face_embedding_error_response(exc.error_code)


async def face_embedding_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    if request.url.path == "/face-embeddings":
        return build_face_embedding_error_response(
            FaceEmbeddingErrorCode.INVALID_IMAGE_FILE
        )
    return await request_validation_exception_handler(request, exc)


async def internal_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.exception(
        "request_failed | requestId=%s | path=%s | errorCode=%s",
        request_id,
        request.url.path,
        FaceEmbeddingErrorCode.INTERNAL_ERROR.value,
    )
    if request.url.path == "/face-embeddings":
        return build_face_embedding_error_response(FaceEmbeddingErrorCode.INTERNAL_ERROR)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )
