"""Đăng ký các công cụ (Tool registration) cho Ingestion Skill.

Module này cung cấp hàm lấy danh sách công cụ nạp tài liệu được xuất bản cho Agent.

Danh sách các hàm / phương thức trong module:
- `get_tools(...)`: Lấy danh sách các công cụ ADK phục vụ cho ingestion skill.
"""

from app.agent.skills.ingestion.batch_agent import create_ingestion_batch_agent_tool
from app.agent.tools.ingestion_tools import (
    get_ingestion_root_tools, review_schema_proposal, review_ingestion_conflict,
    commit_ingestion,
)
from google.adk.tools.function_tool import FunctionTool


def get_tools() -> list:
    """
    Trả về danh sách các công cụ phục vụ cho skill ingestion.

    Returns:
        list: Danh sách các callable tool.
    """
    # Root chỉ điều phối workflow; batch primitives nằm trong ingestion_batch_agent.
    tools = [tool for tool in get_ingestion_root_tools() if tool not in {
        review_schema_proposal, review_ingestion_conflict, commit_ingestion,
    }]
    # The confirmation is enforced by ADK before the callable receives model data.
    tools.append(FunctionTool(func=review_schema_proposal, require_confirmation=True))
    tools.append(FunctionTool(func=review_ingestion_conflict, require_confirmation=True))
    tools.append(FunctionTool(func=commit_ingestion, require_confirmation=True))
    tools.append(create_ingestion_batch_agent_tool())
    return tools


__all__ = ["get_tools"]
