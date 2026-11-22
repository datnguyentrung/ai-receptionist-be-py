"""Đăng ký các công cụ (Tool registration) cho Ingestion Skill.

Module này cung cấp hàm lấy danh sách công cụ nạp tài liệu được xuất bản cho Agent.

Danh sách các hàm / phương thức trong module:
- `get_tools(...)`: Lấy danh sách các công cụ ADK phục vụ cho ingestion skill.
"""

from app.agent.skills.ingestion.batch_agent import ingestion_batch_agent
from app.agent.tools.ingestion_tools import (
    commit_ingestion,
    get_ingestion_root_tools,
    review_entity_resolutions,
    review_ingestion_conflicts,
    review_schema_proposal,
)


def get_tools() -> list:
    """
    Trả về danh sách các công cụ phục vụ cho skill ingestion.

    Returns:
        list: Danh sách các callable tool.
    """
    # Root chỉ điều phối workflow; batch primitives nằm trong ingestion_batch_agent.
    tools = [
        tool
        for tool in get_ingestion_root_tools()
        if tool
        not in {
            review_schema_proposal,
            review_entity_resolutions,
            review_ingestion_conflicts,
            commit_ingestion,
        }
    ]
    # The preceding user message is the confirmation; do not add an A2UI prompt.
    tools.extend((
        review_schema_proposal,
        review_entity_resolutions,
        review_ingestion_conflicts,
        commit_ingestion,
    ))
    tools.append(ingestion_batch_agent)
    return tools


__all__ = ["get_tools"]
