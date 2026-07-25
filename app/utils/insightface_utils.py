import os

import numpy as np

os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")

from insightface.app import FaceAnalysis

# Đồng bộ INSIGHTFACE_HOME với Docker build (download_model.py)
MODEL_DIR = os.path.abspath(
    os.environ.get(
        "INSIGHTFACE_HOME",
        os.path.join(os.path.dirname(__file__), "..", "..", "insightface_data"),
    )
)
os.environ["INSIGHTFACE_HOME"] = MODEL_DIR


def initialize_cpu_face_app():
    """Khởi tạo model nhận diện khuôn mặt tối ưu cho CPU"""
    try:
        os.makedirs(MODEL_DIR, exist_ok=True)
        model_path = os.path.join(MODEL_DIR, "models", "buffalo_s")
        if not os.path.isdir(model_path):
            print(f"InsightFace model not found at: {model_path}")
            print("Run download_model.py before using face recognition.")
            return None

        # Sử dụng model siêu nhẹ buffalo_s và chỉ định rõ dùng CPU
        app = FaceAnalysis(
            name="buffalo_s",
            root=MODEL_DIR,
            providers=["CPUExecutionProvider"],
        )
        # ctx_id = -1 nghĩa là ép buộc chạy trên CPU
        app.prepare(ctx_id=-1, det_size=(640, 640))
        print("InsightFace CPU initialized successfully (Model: buffalo_s).")
        return app
    except Exception as e:
        print(f"InsightFace initialization error: {e!r}")
        return None


# KHỞI TẠO GLOBAL:
# Biến này nằm ngoài hàm để model chỉ load vào RAM đúng 1 lần khi server FastAPI khởi động,
# tránh việc mỗi lần có người điểm danh lại phải load lại model 159MB.
face_app = initialize_cpu_face_app()


def get_face_embedding(img_array: np.ndarray) -> list[float] | None:
    """
    Trích xuất vector khuôn mặt to nhất trong ảnh.
    Trả về list 512 phần tử (float) để lưu vào pgvector.
    """
    if face_app is None:
        raise RuntimeError("InsightFace model is not initialized.")

    try:
        faces = face_app.get(img_array)
        if not faces:
            return None  # Không tìm thấy ai

        # Chọn khuôn mặt có diện tích lớn nhất (người đứng gần màn hình lễ tân nhất)
        largest_face = max(
            faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])
        )

        return largest_face.embedding.tolist()

    except Exception as e:
        raise RuntimeError(f"Face embedding extraction error: {e!r}") from e
