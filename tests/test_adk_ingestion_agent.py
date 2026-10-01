import asyncio
from pathlib import Path

from app.adk_web import liveness, readiness
from app.agent.agent import app as agent_app
from app.agent.skills.skill_loader import discover_skill_descriptors
from app.agent.tools.ingestion_tools import INGESTION_TOOLS, fill_graph_patch


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
        "load_ontology_scope",
        "validate_graph_patch",
        "fill_graph_patch",
        "delete_document",
        "rollback_document_version",
    }


def test_skill_discovery_ignores_cache_directories() -> None:
    skills_dir = Path(__file__).parents[1] / "app" / "agent" / "skills"
    names = {item.name for item in discover_skill_descriptors(skills_dir)}
    assert "ingestion" in names
    assert "__pycache__" not in names


def test_direct_fill_is_blocked_and_health_defaults_not_ready() -> None:
    result = asyncio.run(fill_graph_patch({}, None))
    assert result["success"] is False
    assert result["errors"][0]["code"] == "SOURCE_DOCUMENT_REQUIRED"
    assert asyncio.run(liveness()) == {"status": "UP"}
    assert asyncio.run(readiness()) == {"status": "DOWN", "ready": False}
