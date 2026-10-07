"""Core base models and configuration for Ingestion schemas."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, WithJsonSchema

# Type alias đại diện cho dict[str, Any] tự do nhưng được ghi đè JSON Schema để không chứa
# thuộc tính "additionalProperties".
# Lý do thiết kế: Gemini Developer API báo lỗi cú pháp khi JSON Schema chứa additionalProperties.
FreeformDict = Annotated[dict[str, Any], WithJsonSchema({"type": "object"})]


class IngestionModel(BaseModel):
    """Model cơ sở cho toàn bộ các đối tượng DTO/Schema trong pipeline Ingestion.

    Cấu hình:
    - alias_generator: Tự động chuyển đổi snake_case (Python) sang camelCase (JSON/API/LLM).
    - populate_by_name: Cho phép khởi tạo object bằng cả tên Python gốc lẫn alias camelCase.
    - extra="ignore": Tự động bỏ qua các trường không xác định từ LLM output để tránh lỗi parse.
    - str_strip_whitespace: Tự động cắt bỏ khoảng trắng thừa ở hai đầu chuỗi văn bản.
    """

    model_config = ConfigDict(
        alias_generator=lambda name: "".join(
            word if index == 0 else word.capitalize()
            for index, word in enumerate(name.split("_"))
        ),
        populate_by_name=True,
        extra="ignore",
        str_strip_whitespace=True,
    )


# Alias tương thích
IngestionBaseModel = IngestionModel

__all__ = [
    "FreeformDict",
    "IngestionBaseModel",
    "IngestionModel",
]
