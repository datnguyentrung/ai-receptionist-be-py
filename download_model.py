"""
Script tải trước model buffalo_s của InsightFace vào thư mục insightface_data.
Chạy script này TRONG quá trình build Docker để model được nướng sẵn vào image,
tránh tải lại mỗi khi Render restart server.
"""

import os

from insightface.app import FaceAnalysis

# Ép InsightFace lưu model vào thư mục bên trong project
MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "insightface_data"))
os.environ["INSIGHTFACE_HOME"] = MODEL_DIR

MODEL_NAME = "buffalo_s"


def download():
    os.makedirs(MODEL_DIR, exist_ok=True)
    print(f"INSIGHTFACE_HOME = {MODEL_DIR}")
    print(f"Đang tải model '{MODEL_NAME}'...")

    app = FaceAnalysis(
        name=MODEL_NAME,
        root=MODEL_DIR,
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=-1, det_size=(640, 640))

    model_path = os.path.join(MODEL_DIR, "models", MODEL_NAME)
    if os.path.isdir(model_path):
        print(f"Model '{MODEL_NAME}' đã tải xong tại: {model_path}")
    else:
        print(f"WARNING: Không tìm thấy thư mục model tại {model_path}")

    return app


if __name__ == "__main__":
    download()
