"""Plugin chẩn đoán trôi dạt System Instruction (System Instruction Drift Diagnostic Plugin).

Mô tả:
    Plugin hoạt động tại `before_model_callback` để theo dõi và chẩn đoán sự thay đổi
    của system instruction theo từng agent độc lập trong một session.
    Điều này giúp phân biệt rõ ràng:
    1. System instruction khác nhau giữa các agent riêng biệt trong multi-agent flow
       (đây là hành vi thiết kế đúng, không phải trôi dạt instruction).
    2. System instruction của cùng một agent bị thay đổi giữa các lượt gọi
       (đây mới là trôi dạt thực sự làm mất tiền tố Gemini context cache).
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

from google.adk.plugins.base_plugin import BasePlugin

if TYPE_CHECKING:
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse

logger = logging.getLogger("system_instruction_diagnostics")


def compute_instruction_fingerprint(system_instruction: Any) -> str:
    """Tạo mã băm SHA-256 fingerprint cho system instruction thực tế sau khi render.

    Args:
        system_instruction: Chuỗi hoặc cấu trúc dữ liệu thể hiện system instruction.

    Returns:
        Mã SHA-256 hex string hoàn chỉnh.
    """
    if not system_instruction:
        return hashlib.sha256(b"").hexdigest()

    if isinstance(system_instruction, str):
        raw_text = system_instruction
    elif hasattr(system_instruction, "parts"):
        parts = getattr(system_instruction, "parts") or []
        texts: list[str] = []
        for p in parts:
            if hasattr(p, "text") and p.text:
                texts.append(p.text)
            else:
                texts.append(str(p))
        raw_text = "".join(texts)
    elif isinstance(system_instruction, (list, tuple)):
        raw_text = "".join(str(item) for item in system_instruction)
    else:
        raw_text = str(system_instruction)

    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


class SystemInstructionDiagnosticsPlugin(BasePlugin):
    """Plugin chẩn đoán tính ổn định của system instruction theo từng agent."""

    def __init__(self) -> None:
        super().__init__(name="system_instruction_diagnostics_plugin")
        # Lưu trữ fingerprint theo (session_id, agent_name) -> sha256_hex
        self._tracked_fingerprints: dict[tuple[str, str], str] = {}

    def get_agent_name(self, callback_context: Any) -> str:
        """Trích xuất tên agent hiện tại một cách an toàn."""
        agent_name = getattr(callback_context, "agent_name", None)
        if agent_name:
            return str(agent_name)

        agent = getattr(callback_context, "agent", None)
        if agent is None:
            inv_ctx = getattr(callback_context, "_invocation_context", None)
            agent = getattr(inv_ctx, "agent", None)
        return getattr(agent, "name", "unknown_agent")

    def get_session_id(self, callback_context: Any) -> str:
        """Trích xuất session_id một cách an toàn."""
        session = getattr(callback_context, "session", None)
        if session:
            sess_id = getattr(session, "id", None)
            if sess_id:
                return str(sess_id)

        inv_ctx = getattr(callback_context, "_invocation_context", None)
        if inv_ctx:
            sess = getattr(inv_ctx, "session", None)
            if sess and getattr(sess, "id", None):
                return str(sess.id)

        return "default_session"

    def reset_cache(self) -> None:
        """Xóa toàn bộ bản ghi theo dõi fingerprint (hữu ích cho unit test)."""
        self._tracked_fingerprints.clear()

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        """Kiểm tra fingerprint của system instruction trước khi gửi yêu cầu tới model.

        Lưu ý an toàn:
            Tuyệt đối không log nội dung prompt thô, dữ liệu khách hàng, tool arguments
            hay tool responses để tuân thủ quy tắc bảo mật và PII.
        """
        agent_name = self.get_agent_name(callback_context)
        session_id = self.get_session_id(callback_context)

        # Lấy system_instruction từ config
        config = getattr(llm_request, "config", None)
        system_instruction = getattr(config, "system_instruction", None) if config else None

        fingerprint = compute_instruction_fingerprint(system_instruction)
        short_fp = fingerprint[:12]

        tracking_key = (session_id, agent_name)

        if tracking_key in self._tracked_fingerprints:
            previous_fp = self._tracked_fingerprints[tracking_key]
            if previous_fp != fingerprint:
                prev_short = previous_fp[:12]
                logger.warning(
                    "[SYSTEM_INSTRUCTION_DRIFT] Agent '%s' trong session '%s' có system instruction bị thay đổi "
                    "(fingerprint trước: %s, fingerprint hiện tại: %s, status: DRIFT_DETECTED). "
                    "Điều này có thể làm mất cache prefix của Gemini.",
                    agent_name,
                    session_id,
                    prev_short,
                    short_fp,
                )
                self._tracked_fingerprints[tracking_key] = fingerprint
            else:
                logger.debug(
                    "[SYSTEM_INSTRUCTION_STABLE] Agent '%s' trong session '%s' có system instruction ổn định "
                    "(fingerprint: %s, status: STABLE).",
                    agent_name,
                    session_id,
                    short_fp,
                )
        else:
            self._tracked_fingerprints[tracking_key] = fingerprint
            logger.info(
                "[SYSTEM_INSTRUCTION_INIT] Ghi nhận fingerprint ban đầu cho agent '%s' trong session '%s' "
                "(fingerprint: %s, status: INITIALIZED).",
                agent_name,
                session_id,
                short_fp,
            )

        return None


__all__ = [
    "SystemInstructionDiagnosticsPlugin",
    "compute_instruction_fingerprint",
]
