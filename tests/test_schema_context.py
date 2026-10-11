"""Tests for the shared, read-only schema context boundary."""

import asyncio
from importlib import import_module
from types import SimpleNamespace

import pytest

from app.agent.tools import schema_context_tools
from app.schemas.ingestion.ontology import OntologyProjection, OntologyScopeSummary
from app.services.ingestion.engine import operations
from app.services.ontology.schema_context import SchemaContextService


def _projection(version_id: str = "draft-1") -> OntologyProjection:
    return OntologyProjection(
        version_id=version_id,
        version="v-test",
        digest="digest-1",
        scope_key="training",
        scope_keys=["training"],
        entity_types=[],
        properties=[],
        relationships=[],
        aliases=[],
    )


class _Cache:
    def __init__(self) -> None:
        self.active = SimpleNamespace(version_id="active-1", version="v-active")
        self.calls: list[tuple[str, object]] = []

    async def active_version(self):
        self.calls.append(("active_version", None))
        return self.active

    async def list_scopes(self, version_id: str):
        self.calls.append(("list_scopes", version_id))
        return (
            OntologyScopeSummary(
                id="scope-1",
                ontology_version_id=version_id,
                scope_key="training",
                description="Training",
                schema_hash="scope-digest",
            ),
        )

    async def get_many(self, scope_keys: list[str], version_id: str):
        self.calls.append(("get_many", (scope_keys, version_id)))
        return _projection(version_id)


def test_context_is_deterministic_for_a_pinned_version() -> None:
    cache = _Cache()
    service = SchemaContextService(cache)

    first = asyncio.run(service.load_context(["training"], "draft-1"))
    second = asyncio.run(service.load_context(["training"], "draft-1"))

    assert first == second
    assert first["ontologyVersionId"] == "draft-1"
    assert first["scopeKeys"] == ["training"]
    assert first["schemaDigest"] == "digest-1"


def test_unknown_scope_is_rejected_by_the_shared_service() -> None:
    class MissingScopeCache(_Cache):
        async def get_many(self, _scope_keys, _version_id):
            raise KeyError("Ontology scope does not exist: absent")

    with pytest.raises(KeyError, match="absent"):
        asyncio.run(SchemaContextService(MissingScopeCache()).load_context(["absent"], "draft-1"))


def test_ingestion_wrapper_keeps_the_job_pinned_draft_version() -> None:
    cache = _Cache()

    result = asyncio.run(operations.load_scope(cache, ["training"], "draft-1"))

    assert result["ontologyVersionId"] == "draft-1"
    assert result["schemaDigest"] == "digest-1"
    assert cache.calls == [("get_many", (["training"], "draft-1"))]


def test_graph_qa_tools_are_active_only(monkeypatch) -> None:
    cache = _Cache()
    container = SimpleNamespace(
        ontology_cache=cache,
        schema_context=SchemaContextService(cache),
    )

    async def fake_container():
        return container

    monkeypatch.setattr(schema_context_tools, "get_service_container", fake_container)
    context = SimpleNamespace(state={})
    catalog = asyncio.run(schema_context_tools.list_active_schema_scopes(context))
    loaded = asyncio.run(schema_context_tools.load_active_schema_context(["training"], context))

    assert catalog["ontologyVersionId"] == "active-1"
    assert loaded["ontologyVersionId"] == "active-1"
    assert context.state["graph_qa_schema_context"]["schemaDigest"] == "digest-1"
    graph_qa_tools = import_module("app.agent.skills.graph-qa.tools")
    schema_context_skill_tools = import_module("app.agent.skills.schema-context.tools")
    assert "create_schema_proposal" not in {
        tool.__name__ for tool in graph_qa_tools.get_tools()
    }
    assert "load_active_schema_context" in {
        tool.__name__ for tool in schema_context_skill_tools.get_tools()
    }
