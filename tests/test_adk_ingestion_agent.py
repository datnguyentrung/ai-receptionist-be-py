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
