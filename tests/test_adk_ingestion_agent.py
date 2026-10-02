import asyncio
from pathlib import Path

from app.adk_web import liveness, readiness
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
