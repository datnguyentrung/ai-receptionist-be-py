from fastapi import APIRouter, File, Request, UploadFile

from app.enums.face_embedding_error_code import FaceEmbeddingErrorCode
from app.schemas.face_embedding_schema import FaceEmbeddingResponse
from app.services.face_embedding_service import FaceEmbeddingService

router = APIRouter(tags=["Face Embeddings"])
face_embedding_router = router
IMAGE_FILE = File(
    ...,
    description="Image file containing exactly one face.",
)


ERROR_RESPONSES = {
    code.status_code: {
        "model": FaceEmbeddingResponse,
        "description": code.message,
    }
    for code in FaceEmbeddingErrorCode
}


@router.post(
    "/face-embeddings",
    response_model=FaceEmbeddingResponse,
    responses=ERROR_RESPONSES,
    summary="Generate a face embedding from one uploaded image",
)
async def generate_face_embedding(
    request: Request,
    file: UploadFile = IMAGE_FILE,
) -> FaceEmbeddingResponse:
    service = FaceEmbeddingService()
    request_id = getattr(request.state, "request_id", None)
    return await service.generate_face_embedding(file=file, request_id=request_id)
