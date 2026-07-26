from enum import Enum


class FaceEmbeddingErrorCode(str, Enum):
    INVALID_IMAGE_FILE = "INVALID_IMAGE_FILE"
    EMPTY_IMAGE_FILE = "EMPTY_IMAGE_FILE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_IMAGE_TYPE = "UNSUPPORTED_IMAGE_TYPE"
    IMAGE_DECODE_FAILED = "IMAGE_DECODE_FAILED"
    FACE_NOT_DETECTED = "FACE_NOT_DETECTED"
    MULTIPLE_FACES_DETECTED = "MULTIPLE_FACES_DETECTED"
    FACE_EMBEDDING_FAILED = "FACE_EMBEDDING_FAILED"
    INVALID_EMBEDDING = "INVALID_EMBEDDING"
    MODEL_NOT_INITIALIZED = "MODEL_NOT_INITIALIZED"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    @property
    def status_code(self) -> int:
        return {
            FaceEmbeddingErrorCode.INVALID_IMAGE_FILE: 400,
            FaceEmbeddingErrorCode.EMPTY_IMAGE_FILE: 400,
            FaceEmbeddingErrorCode.UNSUPPORTED_IMAGE_TYPE: 400,
            FaceEmbeddingErrorCode.IMAGE_DECODE_FAILED: 400,
            FaceEmbeddingErrorCode.FILE_TOO_LARGE: 413,
            FaceEmbeddingErrorCode.FACE_NOT_DETECTED: 422,
            FaceEmbeddingErrorCode.MULTIPLE_FACES_DETECTED: 422,
            FaceEmbeddingErrorCode.FACE_EMBEDDING_FAILED: 422,
            FaceEmbeddingErrorCode.INVALID_EMBEDDING: 422,
            FaceEmbeddingErrorCode.MODEL_NOT_INITIALIZED: 503,
            FaceEmbeddingErrorCode.INTERNAL_ERROR: 500,
        }[self]

    @property
    def message(self) -> str:
        return {
            FaceEmbeddingErrorCode.INVALID_IMAGE_FILE: "File upload không hợp lệ",
            FaceEmbeddingErrorCode.EMPTY_IMAGE_FILE: "File không chứa dữ liệu",
            FaceEmbeddingErrorCode.FILE_TOO_LARGE: "File vượt quá dung lượng cho phép",
            FaceEmbeddingErrorCode.UNSUPPORTED_IMAGE_TYPE: "MIME type không được hỗ trợ",
            FaceEmbeddingErrorCode.IMAGE_DECODE_FAILED: "Không thể decode dữ liệu thành ảnh",
            FaceEmbeddingErrorCode.FACE_NOT_DETECTED: "Không phát hiện được khuôn mặt trong ảnh",
            FaceEmbeddingErrorCode.MULTIPLE_FACES_DETECTED: "Ảnh chỉ được chứa một khuôn mặt",
            FaceEmbeddingErrorCode.FACE_EMBEDDING_FAILED: "Không tạo được face embedding",
            FaceEmbeddingErrorCode.INVALID_EMBEDDING: "Face embedding không hợp lệ",
            FaceEmbeddingErrorCode.MODEL_NOT_INITIALIZED: "InsightFace chưa được khởi tạo",
            FaceEmbeddingErrorCode.INTERNAL_ERROR: "Lỗi hệ thống không xác định",
        }[self]
