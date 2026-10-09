"""Public ADK adapter for deterministic structured batch execution."""

import json
import os
from typing import Any

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools.agent_tool import AgentTool
from google.genai import types
from google.genai.types import HttpRetryOptions

from app.agent.skills.ingestion.batch_execution import (
    BatchExecutionModule,
    BatchExtractionOutput,
)

_BATCH_INSTRUCTION = """
Agent này là lớp vỏ tiếp nhận cho một batch nạp tài liệu (ingestion batch). Python sẽ nạp trước dữ liệu,
chọn schema, gửi kết quả xác thực (validation) và trả về JSON quy trình cuối cùng.
Bản thân agent này không có công cụ hàm (function tools).
""".strip()


class IngestionBatchAgentTool(AgentTool):
    """Keep the root contract stable while Python owns the batch lifecycle."""

    def __init__(
        self,
        agent: Agent,
        *,
        executor: BatchExecutionModule | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(agent, **kwargs)
        self._executor = executor or BatchExecutionModule()

    async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> Any:
        request = args.get("request")
        try:
            parsed = json.loads(request) if isinstance(request, str) else None
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict) or set(parsed) != {"ingestionId", "batchIndex"}:
            return {
                "success": False,
                "stage": "batch_request_validation",
                "terminal": True,
                "nextAction": None,
                "errors": [
                    {
                        "code": "INVALID_BATCH_AGENT_REQUEST",
                        "message": "request must be JSON with ingestionId and batchIndex",
                    }
                ],
            }

        ingestion_id = parsed["ingestionId"]
        batch_index = parsed["batchIndex"]
        if (
            not isinstance(ingestion_id, str)
            or not ingestion_id
            or isinstance(batch_index, bool)
            or not isinstance(batch_index, int)
        ):
            return {
                "success": False,
                "stage": "batch_request_validation",
                "terminal": True,
                "nextAction": None,
                "errors": [
                    {
                        "code": "INVALID_BATCH_AGENT_REQUEST",
                        "message": "ingestionId must be non-empty and batchIndex must be an integer",
                    }
                ],
            }

        return await self._executor.execute(ingestion_id, batch_index, tool_context)


def create_ingestion_batch_agent() -> Agent:
    """Build the tool shell; structured extraction agents live behind the adapter."""

    return Agent(
        name="ingestion_batch_agent",
        model=Gemini(
            model=os.getenv("GOOGLE_ADK_MODEL", "gemini-3.5-flash-lite"),
            retry_options=HttpRetryOptions(
                attempts=5,
                initial_delay=3.0,
                max_delay=30.0,
                http_status_codes=[429, 503],
            ),
        ),
        description="Xử lý một batch nạp tài liệu và trả về kết quả quy trình tương ứng.",
        instruction=_BATCH_INSTRUCTION,
        output_schema=BatchExtractionOutput,
        mode="chat",
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.HIGH
            ),
        ),
    )


def create_ingestion_batch_agent_tool() -> AgentTool:
    """Create a fresh AgentTool so each root construction owns its child agent."""

    return IngestionBatchAgentTool(
        agent=create_ingestion_batch_agent(),
        include_plugins=True,
    )


__all__ = [
    "IngestionBatchAgentTool",
    "create_ingestion_batch_agent",
    "create_ingestion_batch_agent_tool",
]
