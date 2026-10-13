"""Shared ToolPlan schema for deterministic tool execution."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class ToolAction(BaseModel):
    """Một hành động gọi tool cụ thể trong kế hoạch."""

    id: str = Field(
        ...,
        description="Định danh duy nhất của action trong plan (ví dụ: 'act_1', 'act_2').",
    )
    tool_name: str = Field(
        ...,
        description="Tên của tool cần gọi (phải thuộc allowlist của domain).",
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Tham số đầu vào cho tool. Có thể chứa tham chiếu đến kết quả của action trước "
            "với cú pháp '${action_id.path}' (ví dụ: '${act_1.result}')."
        ),
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Danh sách ID các action tiên quyết phải hoàn thành thành công trước khi chạy action này.",
    )


class ToolPlan(BaseModel):
    """Kế hoạch thực thi công cụ do ToolPlannerAgent tạo ra."""

    actions: list[ToolAction] = Field(
        default_factory=list,
        description="Danh sách các action cần thực thi.",
    )


__all__ = ["ToolAction", "ToolPlan"]
