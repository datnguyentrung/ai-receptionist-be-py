"""Shared response and session-state helpers for ingestion ADK tools."""

from typing import Any

from google.adk.tools import ToolContext

_CHECKPOINT_KEYS = (
    "success",
    "stage",
    "terminal",
    "retryRequired",
    "nextAction",
    "ingestionId",
    "processedBatches",
    "remainingBatches",
    "nextBatch",
    "batchIndex",
    "scopeKeys",
    "affectedChunkIndexes",
    "errors",
    "validationAttempts",
    "attempt",
    "maxAttempts",
    "repairBatchIndexes",
)


def store_checkpoint(tool_context: ToolContext, result: dict[str, Any]) -> None:
    """Store the compact, agent-facing status of the latest ingestion operation."""
    tool_context.state["ingestion_checkpoint"] = {
        key: result[key] for key in _CHECKPOINT_KEYS if result.get(key) is not None
    }


def tool_exception(
    stage: str, ingestion_id: str | None, exc: Exception
) -> dict[str, Any]:
    """Convert an unexpected tool-boundary exception into the common payload."""
    return {
        "success": False,
        "stage": stage,
        "terminal": True,
        "retryRequired": False,
        "ingestionId": ingestion_id,
        "nextAction": "report_tool_failure",
        "errors": [
            {
                "code": "TOOL_EXECUTION_ERROR",
                "message": str(exc),
                "retryable": False,
            }
        ],
    }


def tool_error(stage: str, code: str, message: str) -> dict[str, Any]:
    """Create a standard payload for an expected business-rule failure."""
    return {
        "success": False,
        "stage": stage,
        "terminal": True,
        "ingestionId": None,
        "nextAction": None,
        "errors": [{"code": code, "message": message}],
    }


__all__ = ["store_checkpoint", "tool_error", "tool_exception"]
