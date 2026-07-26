from enum import Enum


class FaceCheckInErrorCode(Enum):
    FACE_IMAGE_INVALID = (400, "Ảnh khuôn mặt không hợp lệ")
    FACE_NOT_DETECTED = (422, "Không phát hiện được khuôn mặt trong ảnh")
    FACE_NOT_MATCHED = (422, "Khuôn mặt không khớp với dữ liệu đã đăng ký")
    MULTIPLE_FACES_DETECTED = (422, "Ảnh chỉ được chứa một khuôn mặt")
    FACE_CHECK_IN_PERSON_TYPE_INVALID = (
        409,
        "Người được nhận diện không thuộc loại có thể điểm danh",
    )
    PYTHON_BACKEND_UNAVAILABLE = (
        503,
        "Dịch vụ nhận diện khuôn mặt hiện không khả dụng",
    )
    PYTHON_BACKEND_ERROR = (
        502,
        "Dịch vụ nhận diện khuôn mặt trả về dữ liệu không hợp lệ",
    )

    @property
    def status_code(self) -> int:
        return self.value[0]

    @property
    def message(self) -> str:
        return self.value[1]

    def to_response_body(self) -> dict[str, int | str]:
        return {
            "code": self.name,
            "statusCode": self.status_code,
            "message": self.message,
        }


class FaceCheckInException(Exception):
    def __init__(
        self,
        error_code: FaceCheckInErrorCode,
        detail_message: str | None = None,
    ):
        self.error_code = error_code
        self.detail_message = detail_message
        super().__init__(detail_message or error_code.message)
