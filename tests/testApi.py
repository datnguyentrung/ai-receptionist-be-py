import os
import unittest

import requests  # Cài thư viện này bằng: pip install requests nếu chưa có


class TestAttendanceAPI(unittest.TestCase):
    def test_upload_face_image(self):
        # 1. Lấy đường dẫn tuyệt đối đến file ảnh (chống lỗi đường dẫn tương đối)
        current_dir = os.path.dirname(__file__)
        image_path = os.path.join(current_dir, "dat.jpg")

        # 2. Mở file ảnh để chuẩn bị gửi
        with open(image_path, "rb") as img_file:
            # Tạo payload dạng multipart/form-data để mô phỏng Frontend gửi lên
            files = {"file": ("dat.jpg", img_file, "image/jpeg")}

            # 3. Gửi request test API của bạn (Nhớ đổi URL cho đúng)
            # Giả sử API điểm danh của bạn đang chạy ở cổng 8000
            url = "http://localhost:8000/persons/10c12de9-093d-45d4-86e3-9cf5effe8626/face-embedding"

            # Bỏ comment 2 dòng dưới nếu API đang chạy để test thật
            response = requests.post(url, files=files)

            self.assertEqual(response.status_code, 200)

            print(f"Đã load thành công file ảnh tại: {image_path}")


if __name__ == "__main__":
    unittest.main()
