"""Plugin tự động chuẩn hóa và sửa lỗi chính tả tên Tool (Tool Name Auto-Correction / Fuzzy Matcher).

Mô tả:
    Module này cung cấp plugin cho Google ADK để can thiệp vào `after_model_callback`.
    Khi LLM sinh ra Function Call bị lỗi chính tả hoặc thiếu ký tự (ví dụ: `ngestion_batch_agent`),
    plugin sử dụng thuật toán so khớp mờ (difflib) để tự động sửa thành tên tool hợp lệ
    trước khi ADK dispatcher tìm kiếm công cụ, ngăn chặn lỗi `ValueError: Tool '<name>' not found`.
"""

from __future__ import annotations

import difflib
import logging
from typing import TYPE_CHECKING, Any

from google.adk.plugins.base_plugin import BasePlugin

if TYPE_CHECKING:
    from google.adk.agents.base_agent import BaseAgent
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.models.llm_response import LlmResponse

logger = logging.getLogger("adk_tool_autocorrect")


class ToolAutoCorrectPlugin(BasePlugin):
    """Plugin tự động sửa lỗi typo và chuẩn hóa tên công cụ (tool) trong phản hồi của LLM."""

    def __init__(
        self,
        allowed_tools: list[str] | set[str] | None = None,
        cutoff: float = 0.8,
    ) -> None:
        """Khởi tạo plugin chuẩn hóa tên tool.

        Args:
            allowed_tools (list[str] | set[str] | None): Danh sách tên các tool được phép (tùy chọn).
            cutoff (float): Ngưỡng tương đồng tối thiểu (0.0 đến 1.0) để chấp nhận sửa lỗi (mặc định 0.8).
        """
        super().__init__(name="tool_autocorrect_plugin")
        self._allowed_tools: set[str] = set(allowed_tools) if allowed_tools else set()
        self._cutoff = cutoff

    def register_tools(self, tools: list[str] | set[str]) -> None:
        """Đăng ký bổ sung danh sách công cụ hợp lệ vào plugin.

        Args:
            tools (list[str] | set[str]): Danh sách tên tool cần bổ sung.
        """
        self._allowed_tools.update(tools)

    async def before_agent_callback(
        self,
        *,
        agent: BaseAgent,
        callback_context: CallbackContext,
    ) -> Any:
        """Tự động khám phá và cập nhật danh sách tool từ Agent trước khi thực thi.

        Args:
            agent (BaseAgent): Agent sắp được chạy.
            callback_context (CallbackContext): Ngữ cảnh thực thi hiện tại.
        """
        del callback_context
        # 1. Quét danh sách tools được gán trực tiếp trên agent
        agent_tools = getattr(agent, "tools", None) or []
        for t in agent_tools:
            name = getattr(t, "name", None) or getattr(t, "__name__", None)
            if name:
                self._allowed_tools.add(name)
            # 2. Quét các tools nằm trong SkillToolset
            provided = getattr(t, "_provided_tools_by_name", None)
            if isinstance(provided, dict):
                self._allowed_tools.update(provided.keys())
        return None

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        """Xử lý hậu kỳ phản hồi từ mô hình LLM để sửa lỗi tên Function Call trước khi ADK dispatch.

        Args:
            callback_context (CallbackContext): Ngữ cảnh thực thi hiện tại của agent.
            llm_response (LlmResponse): Phản hồi thô vừa nhận được từ mô hình ngôn ngữ.

        Returns:
            LlmResponse | None: Phản hồi đã được chuẩn hóa tên tool (hoặc None nếu không thay đổi).
        """
        del callback_context
        if not self._allowed_tools:
            return None

        content = getattr(llm_response, "content", None)
        if not content or not getattr(content, "parts", None):
            return None

        modified = False
        allowed_list = sorted(self._allowed_tools)

        # 1. Duyệt qua từng part trong nội dung phản hồi của model
        for part in content.parts:
            func_call = getattr(part, "function_call", None)
            if not func_call:
                continue

            current_name = getattr(func_call, "name", None)
            if not current_name or current_name in self._allowed_tools:
                continue

            # 2. Tìm kiếm tên tool gần đúng nhất dựa trên thuật toán difflib
            matches = difflib.get_close_matches(
                current_name,
                allowed_list,
                n=1,
                cutoff=self._cutoff,
            )
            if matches:
                corrected_name = matches[0]
                logger.warning(
                    "[TOOL_AUTOCORRECT] Phát hiện lỗi typo tên tool '%s' -> Tự động sửa thành '%s'",
                    current_name,
                    corrected_name,
                )
                func_call.name = corrected_name
                modified = True

        return llm_response if modified else None


__all__ = ["ToolAutoCorrectPlugin"]
