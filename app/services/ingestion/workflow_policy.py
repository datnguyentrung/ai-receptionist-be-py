"""Declarative workflow policy for ingestion tool/state transitions.

The language model may request an invalid next step.  This module is the deterministic
source of truth that decides whether an operation is legal for the current job state.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowDecision:
    allowed: bool
    terminal: bool
    next_action: str | None


WORKFLOW_POLICY: dict[str, frozenset[str]] = {
    "BATCHING": frozenset({
        "get_batch",
        "list_scopes",
        "load_scopes",
        "submit_batch",
        "finalize",
        "create_schema_proposal",
        "get_status",
    }),
    "BLOCKED_SCHEMA": frozenset({
        "get_status",
        "get_schema_proposal",
        "review_schema_proposal",
        "apply_schema_proposal",
        "rebase_ingestion",
    }),
    "READY": frozenset({"get_status", "fill"}),
    "COMMITTED": frozenset({"get_status", "fill"}),
    "FAILED": frozenset({"get_status"}),
}

TERMINAL_NEXT_ACTION: dict[str, str | None] = {
    "FAILED": "explicit_extraction_failure",
    "COMMITTED": None,
    "DELETED": None,
    "ROLLED_BACK": None,
}


def normalize_status(status: Any) -> str:
    value = getattr(status, "value", status)
    return str(value)


def evaluate_workflow(status: Any, action: str) -> WorkflowDecision:
    status_key = normalize_status(status)
    allowed_actions = WORKFLOW_POLICY.get(status_key, frozenset({"get_status"}))
    terminal = status_key in TERMINAL_NEXT_ACTION
    return WorkflowDecision(
        allowed=action in allowed_actions,
        terminal=terminal,
        next_action=TERMINAL_NEXT_ACTION.get(status_key),
    )


__all__ = [
    "TERMINAL_NEXT_ACTION",
    "WORKFLOW_POLICY",
    "WorkflowDecision",
    "evaluate_workflow",
    "normalize_status",
]
