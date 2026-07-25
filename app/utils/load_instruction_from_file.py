import os


def load_instruction_from_file(file_path):
    # __file__ đang là vị trí của load_instruction_from_file.py
    # os.path.dirname(__file__) sẽ là thư mục utils
    # os.path.dirname(...) lần nữa sẽ lùi ra thư mục gốc Shopping_Research_Agent
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

    # Ghép path chuẩn
    full_path = os.path.join(base_dir, file_path)

    if not os.path.exists(full_path):
        raise FileNotFoundError(f"Instruction file not found: {full_path}")

    with open(full_path, "r", encoding="utf-8") as f:
        return f.read()
