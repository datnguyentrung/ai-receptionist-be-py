"""Regression tests for deterministic structured batch orchestration."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.agent.skills.ingestion import batch_execution
from app.agent.skills.ingestion.batch_agent import (
    IngestionBatchAgentTool,
    create_ingestion_batch_agent,
)
from app.agent.skills.ingestion.batch_execution import (
    BatchExecutionModule,
    BatchExtractionOutput,
    ScopeSelectionOutput,
    _PreparedBatch,
)
from app.schemas.ingestion.semantic_patch import SemanticGraphPatchFragment


class FakeBatchModel:
    def __init__(self, *, selected: list[str] | None = None) -> None:
        self.selected = selected or ["training"]
        self.calls: list[str] = []

    async def select_scopes(self, _payload, _context) -> ScopeSelectionOutput:
        self.calls.append("select")
        return ScopeSelectionOutput(scope_keys=self.selected)

    async def extract(self, payload, _context) -> BatchExtractionOutput:
        self.calls.append("extract")
        return BatchExtractionOutput(
            scope_keys=payload.get("requiredScopeKeys", self.selected),
            extraction=SemanticGraphPatchFragment.model_construct(
                nodes=[], edges=[], coverage=[], warnings=[]
            ),
        )

    async def repair(self, _payload, _context):
        self.calls.append("repair")
        raise AssertionError("repair is not expected")


def _prepared(scope_keys: list[str]) -> _PreparedBatch:
    workspace = SimpleNamespace(
        job=SimpleNamespace(
            id="ing-1", scope_hint=None, ontology_version_id="version-1"
        )
    )
    batch = SimpleNamespace(status="PENDING", scope_keys=scope_keys, batch_index=0)
    return _PreparedBatch(
        container=SimpleNamespace(repository=object(), ontology_cache=object()),
        workspace=workspace,
        batch=batch,
        payload={
            "batch": {"batchIndex": 0, "chunks": [{"text": "x"}]},
            "scopeCatalog": [{"scopeKey": "training", "description": "training"}],
        },
        catalog=[{"scopeKey": "training", "description": "training"}],
        catalog_keys=["training"],
        version_id="version-1",
    )


def _install_execution_fakes(
    monkeypatch, module: BatchExecutionModule, prepared: _PreparedBatch
) -> None:
    async def fake_prepare(*_args):
        return prepared

    async def fake_load_scope(*_args):
        return {
            "scope": {
                "scopeKeys": ["training"],
                "entityTypes": [],
                "properties": [],
                "relationships": [],
            }
        }

    async def fake_reconcile(_prepared, selected, _extraction):
        return selected

    async def fake_submit(_prepared, scopes, _extraction, _context):
        return (
            {
                "success": True,
                "stage": "batching",
                "submittedBatchIndex": 0,
                "scopeKeys": scopes,
                "nextBatch": 1,
            },
            object(),
        )

    monkeypatch.setattr(module, "_prepare", fake_prepare)
    monkeypatch.setattr(module, "_reconcile_scope_keys", fake_reconcile)
    monkeypatch.setattr(module, "_submit", fake_submit)
    monkeypatch.setattr(batch_execution.operations, "load_scope", fake_load_scope)
    monkeypatch.setattr(
        batch_execution, "log_ingestion_event", lambda *_args, **_kwargs: None
    )


def test_selected_scope_uses_one_structured_extraction_call(monkeypatch) -> None:
    model = FakeBatchModel()
    module = BatchExecutionModule(model, prompt_max_bytes=1)
    _install_execution_fakes(monkeypatch, module, _prepared(["training"]))

    result = asyncio.run(module.execute("ing-1", 0, SimpleNamespace(state={})))

    assert result["success"] is True
    assert model.calls == ["extract"]


def test_large_ambiguous_prompt_selects_then_extracts(monkeypatch) -> None:
    model = FakeBatchModel()
    module = BatchExecutionModule(model, prompt_max_bytes=1)
    _install_execution_fakes(monkeypatch, module, _prepared([]))

    result = asyncio.run(module.execute("ing-1", 0, SimpleNamespace(state={})))

    assert result["success"] is True
    assert model.calls == ["select", "extract"]


def test_public_tool_rejects_invalid_request_without_execution() -> None:
    class NeverCalled:
        async def execute(self, *_args):
            raise AssertionError("invalid requests must not reach the executor")

    tool = IngestionBatchAgentTool(
        create_ingestion_batch_agent(), executor=NeverCalled()
    )
    result = asyncio.run(
        tool.run_async(
            args={"request": "not json"}, tool_context=SimpleNamespace(state={})
        )
    )

    assert result["success"] is False
    assert result["errors"][0]["code"] == "INVALID_BATCH_AGENT_REQUEST"


def test_public_shell_and_internal_structured_agents_expose_no_tools() -> None:
    agent = create_ingestion_batch_agent()
    assert agent.mode == "single_turn"
    assert agent.tools == []
    assert agent.output_schema is BatchExtractionOutput
