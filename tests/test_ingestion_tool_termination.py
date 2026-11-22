from types import SimpleNamespace

import pytest

from app.utils.ingestion_logger import (
    ADKDetailedLoggerPlugin,
    should_stop_after_tool_result,
)


def test_terminal_tool_failure_keeps_turn_for_user_report() -> None:
    assert not should_stop_after_tool_result(
        {
            "success": False,
            "terminal": True,
            "nextAction": "report_tool_failure",
        }
    )


def test_terminal_commit_success_keeps_model_turn_for_final_report() -> None:
    assert not should_stop_after_tool_result(
        {
            "success": True,
            "stage": "committed",
            "terminal": True,
            "nextAction": None,
            "commitStatus": "COMMITTED",
            "readbackVerified": True,
        }
    )


def test_schema_proposal_pending_stops_turn() -> None:
    assert should_stop_after_tool_result(
        {
            "success": True,
            "stage": "awaiting_schema_approval",
            "nextAction": "wait_for_schema_review",
            "pendingProposalCount": 1,
            "validationErrors": [
                {
                    "code": "SCHEMA_PROPOSAL_PENDING",
                    "message": "Schema proposal is waiting for review",
                }
            ],
        }
    )


def test_schema_proposal_in_begin_payload_stops_turn() -> None:
    assert should_stop_after_tool_result(
        {
            "success": True,
            "stage": "awaiting_schema_approval",
            "nextAction": "wait_for_schema_review",
            "pendingSchemaProposal": {
                "proposalId": "proposal-1",
                "proposalStatus": "PROPOSED",
            },
            "validationErrors": [{"code": "SCHEMA_PROPOSAL_PENDING"}],
        }
    )


def test_stale_schema_proposal_error_without_pending_proposal_does_not_stop_turn() -> None:
    assert not should_stop_after_tool_result(
        {
            "success": True,
            "stage": "awaiting_schema_approval",
            "nextAction": "wait_for_schema_review",
            "pendingProposalCount": 0,
            "validationErrors": [
                {
                    "code": "SCHEMA_PROPOSAL_PENDING",
                    "message": "Schema proposal was already approved",
                }
            ],
        }
    )


def test_stale_schema_proposal_error_in_repair_context_does_not_stop_turn() -> None:
    assert not should_stop_after_tool_result(
        {
            "success": True,
            "terminal": False,
            "nextAction": "repair_batch",
            "repairContext": {
                "validationIssues": [{"code": "SCHEMA_PROPOSAL_PENDING"}]
            },
        }
    )


def test_existing_schema_object_recovery_does_not_stop_turn() -> None:
    assert not should_stop_after_tool_result(
        {
            "success": False,
            "stage": "schema_rebase_required",
            "terminal": False,
            "nextAction": "rebase_ingestion",
            "errors": [{"code": "SCHEMA_OBJECT_ALREADY_EXISTS"}],
        }
    )


def test_non_terminal_tool_result_does_not_stop_turn() -> None:
    assert not should_stop_after_tool_result(
        {
            "success": True,
            "terminal": False,
            "nextAction": "process_batch",
        }
    )


@pytest.mark.anyio
async def test_after_tool_callback_keeps_model_after_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.utils.ingestion_logger.write_raw_trace",
        lambda *args, **kwargs: None,
    )
    context = SimpleNamespace(
        actions=SimpleNamespace(skip_summarization=None),
    )
    plugin = ADKDetailedLoggerPlugin()

    await plugin.after_tool_callback(
        tool=SimpleNamespace(name="rebase_ingestion"),
        tool_args={},
        tool_context=context,
        result={
            "success": False,
            "terminal": True,
            "nextAction": "report_tool_failure",
        },
    )

    assert context.actions.skip_summarization is None
