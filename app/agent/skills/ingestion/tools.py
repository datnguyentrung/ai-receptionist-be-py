"""Đăng ký các công cụ (Tool registration) cho Ingestion Skill.

Module này cung cấp hàm lấy danh sách công cụ nạp tài liệu được xuất bản cho Agent.

Danh sách các hàm / phương thức trong module:
- `get_tools(...)`: Lấy danh sách các công cụ ADK phục vụ cho ingestion skill.
"""

from app.agent.skills.ingestion.batch_agent import create_ingestion_batch_agent_tool
from app.agent.tools.ingestion_tools import get_ingestion_root_tools


def get_tools() -> list:
    """
    Trả về danh sách các công cụ phục vụ cho skill ingestion.

    Returns:
        list: Danh sách các callable tool.
    """
    # Root chỉ điều phối workflow; batch primitives nằm trong ingestion_batch_agent.
    return [*get_ingestion_root_tools(), create_ingestion_batch_agent_tool()]


__all__ = ["get_tools"]
