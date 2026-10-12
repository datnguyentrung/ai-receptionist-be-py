"""Routing tools for the Root Router Agent."""

from typing import Any


def ask_clarifying_question(
    question: str,
    options: list[str],
    ambiguity_type: str,
    reasoning: str,
    candidate_agents: list[str],
) -> dict[str, Any]:
    """Hỏi lại người dùng để làm rõ ý định khi yêu cầu còn mơ hồ hoặc có sự giao thoa giữa các domain.

    Args:
        question: Câu hỏi ngắn gọn, tự nhiên gửi tới người dùng (dưới 25 từ).
        options: 2-3 lựa chọn đại diện phương án (mỗi option dưới 8 từ).
        ambiguity_type: Loại mơ hồ (ví dụ: 'domain_overlap', 'vague_intent').
        reasoning: Lý do cần làm rõ.
        candidate_agents: Danh sách tên các agent đang cạnh tranh xử lý (INGESTION_AGENT, CALCULATION_AGENT, GRAPH_QA_AGENT).
    """
    return {
        "status": "clarification_needed",
        "question": question,
        "options": options,
        "ambiguity_type": ambiguity_type,
        "reasoning": reasoning,
        "candidate_agents": candidate_agents,
    }


__all__ = ["ask_clarifying_question"]
