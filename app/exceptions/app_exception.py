from app.exceptions.error_code import ErrorCode


class AppException(Exception):
    def __init__(self, error_code: ErrorCode, detail_message: str = None):
        self.error_code = error_code
        # Nếu có thông báo chi tiết (vd: message gốc từ DB), thì nối thêm vào
        self.detail_message = detail_message
        super().__init__(self.error_code.message)
