from enum import Enum


class ErrorCode(Enum):
    # (HTTP Status, Error Message)
    PERSON_NOT_FOUND = (404, "Không tìm thấy thông tin người")
    CLASS_NOT_FOUND = (404, "Lớp học không tồn tại")
    ALREADY_CHECKED_IN = (409, "Học viên đã điểm danh ca học này")
    NO_VALID_SESSION = (404, "Không tìm thấy ca học nào phù hợp đang mở")
    INVALID_IMAGE = (400, "Ảnh tải lên không hợp lệ hoặc không có khuôn mặt")
    JAVA_SYSTEM_ERROR = (500, "Có lỗi từ hệ thống nghiệp vụ (Java)")
    AI_SYSTEM_ERROR = (500, "Có lỗi từ hệ thống AI nhận diện")
    JAVA_TIMEOUT = (503, "Hệ thống nghiệp vụ (Java) đang bảo trì hoặc phản hồi chậm")
    UNCATEGORIZED_EXCEPTION = (500, "Lỗi hệ thống không xác định")

    @property
    def status_code(self) -> int:
        return self.value[0]

    @property
    def message(self) -> str:
        return self.value[1]
