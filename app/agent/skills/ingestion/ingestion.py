"""Định nghĩa và đóng gói Ingestion Skill cho Agent (Product Sales Knowledge Graph ingestion skill).

Module này phụ trách nạp tệp SKILL.md, render các giá trị động và tạo đối tượng skill hoàn chỉnh.

Danh sách các hàm / phương thức trong module:
- `_build_ingestion_substitutions(...)`: Xây dựng từ điển các giá trị động được tiêm vào SKILL.md.
- `build_skill(...)`: Xây dựng đối tượng Skill từ thư mục hiện tại để làm mới prompt/hướng dẫn khi có thay đổi.
"""

from pathlib import Path

from app.agent.skills.skill_template import (
    load_rendered_skill_from_dir,
)

_SKILL_DIR = Path(__file__).resolve().parent


def _build_ingestion_substitutions() -> dict[str, str]:
    """
    Xây dựng các giá trị thay thế động để tiêm vào tệp SKILL.md của ingestion.

    Returns:
        dict[str, str]: Từ điển ánh xạ {placeholder: dynamic_value}.
    """
    # 1. Trả về từ điển các tham số thay thế động
    return {}


def build_skill():
    """
    Xây dựng đối tượng Skill hoàn chỉnh từ thư mục chứa skill hiện tại.

    Returns:
        Skill: Đối tượng skill đã render đầy đủ hướng dẫn.
    """
    # 1. Nạp và render skill template từ thư mục _SKILL_DIR
    return load_rendered_skill_from_dir(
        _SKILL_DIR,
        _build_ingestion_substitutions(),
    )


# Khởi tạo instance của ingestion_skill để sẵn sàng export
ingestion_skill = build_skill()


__all__ = ["build_skill", "ingestion_skill"]
