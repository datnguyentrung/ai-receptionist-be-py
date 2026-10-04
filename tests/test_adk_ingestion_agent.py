import asyncio
from pathlib import Path

from google.genai import types

from app.adk_web import liveness, readiness
from app.agent.agent import _compact_ingestion_context
from app.agent.agent import app as agent_app
from app.agent.skills.skill_loader import discover_skill_descriptors
from app.agent.tools.graphrag_tools import retrieve_taekwondo_knowledge
from app.agent.tools.ingestion_tools import INGESTION_TOOLS


def test_agent_and_ingestion_tools_are_discoverable() -> None:
    assert agent_app.name == "taekwondo_ingestion"
    names = {tool.__name__ for tool in INGESTION_TOOLS}
    assert names == {
        "begin_ingestion",
        "get_ingestion_batch",
        "submit_ingestion_batch",
        "finalize_ingestion",
        "fill_ingestion",
        "get_ingestion_status",
        "list_ontology_scopes",
        "load_ontology_scopes",
        "create_schema_proposal",
        "get_schema_proposal",
        "review_schema_proposal",
        "apply_schema_proposal",
        "rebase_ingestion",
        "delete_document",
        "rollback_document_version",
    }
    assert "repair_ingestion_batch" not in names


def test_skill_discovery_ignores_cache_directories() -> None:
    skills_dir = Path(__file__).parents[1] / "app" / "agent" / "skills"
    names = {item.name for item in discover_skill_descriptors(skills_dir)}
    assert "ingestion" in names
    assert "graph-qa" in names
    assert "__pycache__" not in names


def test_health_defaults_not_ready() -> None:
    assert asyncio.run(liveness()) == {"status": "UP"}
    assert asyncio.run(readiness()) == {"status": "DOWN", "ready": False}


def test_public_retrieval_tool_is_discoverable() -> None:
    assert retrieve_taekwondo_knowledge.__name__ == "retrieve_taekwondo_knowledge"


def test_compacted_checkpoint_preserves_terminal_control_fields() -> None:
    contents = [
        types.Content(role="user", parts=[types.Part(text="ingest this document")]),
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="submit_ingestion_batch",
                        response={
                            "ingestionId": "ing-1",
                            "success": False,
                            "stage": "explicit_extraction_failure",
                            "terminal": True,
                            "retryRequired": False,
                            "nextAction": "explicit_extraction_failure",
                            "attempt": 3,
                            "maxAttempts": 2,
                            "errors": [{"code": "BATCH_VALIDATION_RETRY_LIMIT_EXCEEDED"}],
                            "graphFragment": {"nodes": [{"tempId": "huge"}]},
                            "semanticFragment": {"nodes": [{"tempId": "huge"}]},
                            "chunks": [{"chunkIndex": 0, "text": "very large source"}],
                            "mergedSchemaHash": "large-schema-state",
                        },
                    )
                )
            ],
        ),
    ]

    compacted = _compact_ingestion_context(contents)
    checkpoint = next(
        part.text
        for content in compacted
        for part in (content.parts or [])
        if part.text and part.text.startswith("INGESTION_CHECKPOINT")
    )

    assert '"terminal": true' in checkpoint
    assert '"retryRequired": false' in checkpoint
    assert '"nextAction": "explicit_extraction_failure"' in checkpoint
    assert "graphFragment" not in checkpoint
    assert "semanticFragment" not in checkpoint
    assert '"chunks"' not in checkpoint
    assert "mergedSchemaHash" not in checkpoint


def test_repair_compaction_drops_previous_batch_payloads_across_retries() -> None:
    def tool_response(name: str, response: dict) -> types.Content:
        return types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name=name,
                        response=response,
                    )
                )
            ],
        )

    contents = [
        types.Content(role="user", parts=[types.Part(text="ingest document")]),
        tool_response(
            "get_ingestion_batch",
            {"batch": {"chunks": [{"text": "RAW_BATCH_1"}]}},
        ),
        tool_response(
            "load_ontology_scopes",
            {"schema": "RAW_SCHEMA_1"},
        ),
        tool_response(
            "submit_ingestion_batch",
            {
                "ingestionId": "ing-1",
                "stage": "repair_required",
                "terminal": False,
                "retryRequired": True,
                "nextAction": "repair_batch",
                "batchIndex": 1,
                "scopeKeys": ["training"],
                "errors": [{"code": "EVIDENCE_NOT_GROUNDED"}],
                "graphFragment": {"debug": "OLD_FRAGMENT_1"},
            },
        ),
        tool_response(
            "get_ingestion_batch",
            {"batch": {"chunks": [{"text": "RAW_BATCH_2"}]}},
        ),
        tool_response(
            "load_ontology_scopes",
            {"schema": "RAW_SCHEMA_2"},
        ),
        tool_response(
            "submit_ingestion_batch",
            {
                "ingestionId": "ing-1",
                "stage": "repair_required",
                "terminal": False,
                "retryRequired": True,
                "nextAction": "repair_batch",
                "batchIndex": 1,
                "scopeKeys": ["training"],
                "affectedChunkIndexes": [10, 11],
                "errors": [{"code": "EVIDENCE_NOT_GROUNDED"}],
                "graphFragment": {"debug": "OLD_FRAGMENT_2"},
            },
        ),
    ]

    compacted = _compact_ingestion_context(contents)
    serialized = "\n".join(content.model_dump_json() for content in compacted)

    assert "RAW_BATCH_1" not in serialized
    assert "RAW_SCHEMA_1" not in serialized
    assert "OLD_FRAGMENT_1" not in serialized
    assert "RAW_BATCH_2" not in serialized
    assert "RAW_SCHEMA_2" not in serialized
    assert "OLD_FRAGMENT_2" not in serialized

    checkpoint = next(
        part.text
        for content in compacted
        for part in (content.parts or [])
        if part.text and part.text.startswith("INGESTION_CHECKPOINT")
    )
    assert '"batchIndex": 1' in checkpoint
    assert "EVIDENCE_NOT_GROUNDED" in checkpoint
    assert "affectedChunkIndexes" in checkpoint


def test_rebase_compaction_drops_proposal_and_intermediate_history() -> None:
    def tool_response(name: str, response: dict) -> types.Content:
        return types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name=name,
                        response=response,
                    )
                )
            ],
        )

    contents = [
        types.Content(role="user", parts=[types.Part(text="ingest document")]),
        tool_response("submit_ingestion_batch", {"ingestionId": "ing-1", "stage": "schema_gap_candidate"}),
        tool_response("create_schema_proposal", {"proposalId": "prop-123", "technicalName": "achievement"}),
        tool_response("review_schema_proposal", {"proposalId": "prop-123", "approved": True}),
        tool_response("apply_schema_proposal", {"version": "v1.1.1"}),
        tool_response(
            "rebase_ingestion",
            {
                "ingestionId": "ing-1",
                "success": True,
                "stage": "batching",
                "nextAction": "process_batch",
                "processedBatches": 0,
                "remainingBatches": 5,
                "workspaceStats": {"chunks": 23, "batches": 5, "stagedBatches": 0},
            },
        ),
    ]

    compacted = _compact_ingestion_context(contents)
    serialized = "\n".join(content.model_dump_json() for content in compacted)

    assert "schema_gap_candidate" not in serialized
    assert "create_schema_proposal" not in serialized
    assert "prop-123" not in serialized

    checkpoint = next(
        part.text
        for content in compacted
        for part in (content.parts or [])
        if part.text and part.text.startswith("INGESTION_CHECKPOINT")
    )
    assert '"stage": "batching"' in checkpoint
    assert '"processedBatches": 0' in checkpoint
    assert '"workspaceStats"' in checkpoint


def test_adk_plugin_rate_limit_retry_handler() -> None:
    from unittest.mock import MagicMock

    from app.utils.ingestion_logger import ADKDetailedLoggerPlugin

    plugin = ADKDetailedLoggerPlugin()

    # Mock agent and canonical model
    mock_llm = MagicMock()
    mock_response = MagicMock()

    async def fake_gen(*args, **kwargs):
        yield mock_response

    mock_llm.generate_content_async = MagicMock(side_effect=fake_gen)

    mock_agent = MagicMock()
    mock_agent.name = "root_agent"
    mock_agent.canonical_model = mock_llm

    mock_ctx = MagicMock()
    mock_ctx.agent = mock_agent

    mock_request = MagicMock()
    error = RuntimeError("429 RESOURCE_EXHAUSTED. Please retry in 0.1s.")

    # Call on_model_error_callback
    res = asyncio.run(
        plugin.on_model_error_callback(
            callback_context=mock_ctx,
            llm_request=mock_request,
            error=error,
        )
    )

    assert res is mock_response
    assert mock_llm.generate_content_async.called
