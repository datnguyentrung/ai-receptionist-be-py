"""Typed public boundary for one deterministic ingestion batch."""

from typing import Any

from app.agent.skills.ingestion.batch_execution import BatchExecutionModule


async def ingestion_batch_agent(
    ingestion_id: str,
    batch_index: int,
    tool_context: Any,
) -> dict[str, Any]:
    """Process exactly one batch selected by the persisted ingestion workflow."""
    return await BatchExecutionModule().execute(ingestion_id, batch_index, tool_context)


__all__ = ["ingestion_batch_agent"]
