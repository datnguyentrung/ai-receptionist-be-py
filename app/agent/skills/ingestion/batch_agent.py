"""Specialized ADK agent used as the ingestion batch tool."""

import json
import os
from typing import Any

from google.adk.agents import Agent
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from app.agent.tools.ingestion_tools import get_ingestion_batch_tools


_BATCH_INSTRUCTION = """
You process exactly one ingestion batch delegated by the root agent.

The AgentTool request is a JSON string containing exactly `ingestionId` and `batchIndex`.
Follow this procedure:
1. Call get_ingestion_batch once for this batch. If it is already staged, return its
   summary immediately. Reuse its cached payload for every repair; never fetch it again.
2. Use the returned canonicalGraphContext as prior-batch entity context. Select ontology
   scopes only when none have been selected, then load those scopes.
3. Extract nodes and edges into SemanticGraphPatchFragment. Every node, edge, property,
   and coverage record must be grounded in the returned batch chunks and use the loaded
   ontology names.
4. Submit the fragment. When validation requests repair, call repair_ingestion_batch with
   SemanticGraphRepairDelta and retry only this batch. Do not re-fetch chunks.
5. Stop and return the exact JSON tool result when the batch is staged, terminal, or requires
   schema review/rebase. Schema proposals, reviews, rebases, finalization, and persistence
   belong to the root agent.

Do not invent persistence or validation rules: the ingestion tools and services enforce
identity resolution, graph validation, and canonical node/edge handling.
""".strip()


class IngestionBatchAgentTool(AgentTool):
    """Avoid a child run when the shared accumulator already staged this batch."""

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
                "errors": [{"code": "INVALID_BATCH_AGENT_REQUEST", "message": "request must be JSON with ingestionId and batchIndex"}],
            }

        ingestion_id = parsed["ingestionId"]
        batch_index = parsed["batchIndex"]
        if not isinstance(ingestion_id, str) or not ingestion_id or isinstance(batch_index, bool) or not isinstance(batch_index, int):
            return {
                "success": False,
                "stage": "batch_request_validation",
                "terminal": True,
                "nextAction": None,
                "errors": [{"code": "INVALID_BATCH_AGENT_REQUEST", "message": "ingestionId must be non-empty and batchIndex must be an integer"}],
            }

        accumulator = tool_context.state.get("ingestion_batch_accumulator")
        if isinstance(accumulator, dict) and accumulator.get("ingestionId") == ingestion_id:
            summary = (accumulator.get("batches") or {}).get(str(batch_index))
            if isinstance(summary, dict) and summary.get("status") == "STAGED":
                return {**summary, "success": True, "idempotent": True}
        return await super().run_async(args=args, tool_context=tool_context)


def create_ingestion_batch_agent() -> Agent:
    """Build the narrow agent responsible for one ingestion batch lifecycle."""

    return Agent(
        name="ingestion_batch_agent",
        model=os.getenv("GOOGLE_ADK_MODEL", "gemini-3.5-flash-lite"),
        description="Processes one ingestion batch and returns its workflow result.",
        instruction=_BATCH_INSTRUCTION,
        tools=get_ingestion_batch_tools(),
        generate_content_config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MEDIUM),
        ),
    )


def create_ingestion_batch_agent_tool() -> AgentTool:
    """Create a fresh AgentTool so each root construction owns its child agent."""

    return IngestionBatchAgentTool(
        agent=create_ingestion_batch_agent(),
        include_plugins=False,
    )


__all__ = [
    "create_ingestion_batch_agent",
    "create_ingestion_batch_agent_tool",
    "IngestionBatchAgentTool",
]
