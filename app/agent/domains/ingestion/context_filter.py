"""Context compaction logic for the Ingestion domain."""

from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.genai import types


def compact_ingestion_context(contents: list[types.Content]) -> list[types.Content]:
    """
    Rút gọn lịch sử hội thoại của Agent trong quá trình ingestion.

    Mục đích:
        - Giảm context window, token cost và nhiễu từ các batch cũ.
        - Giữ nguyên yêu cầu ban đầu của User.
        - Giữ cặp Function Call/Response của load_skill.
        - Giữ cặp Function Call/Response của ingestion_batch_agent gần nhất.
        - Giữ toàn bộ nội dung phát sinh sau batch gần nhất.
        - Bảo toàn thứ tự thời gian của các Content được giữ lại.

    Nguyên tắc:
        Chỉ loại bỏ lịch sử không cần thiết, không chỉnh sửa nội dung
        của các Content được giữ lại.

    Lưu ý:
        Giả định các tool call được xử lý tuần tự và mỗi call có response.
        Chưa xử lý đầy đủ parallel tool calls hoặc thought signatures.
    """
    calls, pairs = {}, {}

    for i, content in enumerate(contents):
        for part in content.parts or []:
            if part.function_call:
                calls[part.function_call.name] = i
            elif part.function_response:
                name = part.function_response.name
                if name in calls:
                    pairs[name] = (calls.pop(name), i)

    batch = pairs.get("ingestion_batch_agent")

    if not batch:
        return contents

    first_user = next(
        (
            i
            for i, c in enumerate(contents)
            if c.role == "user"
            and any(p.function_response is None for p in c.parts or [])
        ),
        None,
    )

    keep = set(batch) | set(pairs.get("load_skill", ()))

    if first_user is not None:
        keep.add(first_user)

    return [c for i, c in enumerate(contents) if i in keep or i > batch[1]]


def compact_ingestion_before_model(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> None:
    """Chỉ compact context khi Ingestion Agent gọi LLM."""
    del callback_context
    if llm_request.contents:
        llm_request.contents = compact_ingestion_context(llm_request.contents)
    return None


__all__ = ["compact_ingestion_context", "compact_ingestion_before_model"]
