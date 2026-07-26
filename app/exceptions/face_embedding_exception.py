from app.enums.face_embedding_error_code import FaceEmbeddingErrorCode


class FaceEmbeddingException(Exception):
    def __init__(
        self,
        error_code: FaceEmbeddingErrorCode,
        detail_message: str | None = None,
    ) -> None:
        self.error_code = error_code
        self.detail_message = detail_message
        super().__init__(detail_message or error_code.message)
