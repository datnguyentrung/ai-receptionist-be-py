"""ADK tools for Ingestion Skill (Template / Boilerplate).

Chứa các công cụ mẫu làm khung giao tiếp giữa Agent và backend xử lý tài liệu.
"""

from typing import Any
from google.adk.tools import ToolContext


async def process_document_sample(
    artifact_name: str,
    tool_context: ToolContext,
    scope_hint: str | None = None,
) -> dict[str, Any]:
    """Công cụ mẫu: Tải và xử lý tài liệu từ ADK session artifacts.

    Args:
        artifact_name: Tên file artifact đính kèm trong session.
        tool_context: Context thực thi công cụ của ADK.
        scope_hint: Gợi ý phạm vi/chủ đề của tài liệu.
    """
    artifact = await tool_context.load_artifact(artifact_name)
    if not artifact or not artifact.inline_data or not artifact.inline_data.data:
        return {
            "success": False,
            "error": f"Không tìm thấy file artifact: {artifact_name}",
        }

    return {
        "success": True,
        "artifactName": artifact_name,
        "scopeHint": scope_hint,
        "message": "Đã tiếp nhận tài liệu mẫu thành công.",
    }


async def extract_knowledge_sample(
    content_text: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Công cụ mẫu: Trích xuất tri thức hoặc thực thể từ văn bản.

    Args:
        content_text: Nội dung văn bản cần trích xuất.
        tool_context: Context thực thi công cụ của ADK.
    """
    return {
        "success": True,
        "extractedEntities": [],
        "charCount": len(content_text),
        "message": "Trích xuất mẫu hoàn tất.",
    }


def get_ingestion_tools() -> list:
    """Trả về danh sách các tools đăng ký cho skill ingestion."""
    return [
        process_document_sample,
        extract_knowledge_sample,
    ]


__all__ = [
    "process_document_sample",
    "extract_knowledge_sample",
    "get_ingestion_tools",
]
