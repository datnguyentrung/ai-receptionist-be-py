"""Regression tests for deterministic structured batch orchestration."""

import asyncio
from types import SimpleNamespace
from typing import Any

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

    async def validate_evidence(self, _payload, _context):
        self.calls.append("validate_evidence")
        return batch_execution.SemanticEvidenceValidationResult(decisions=[])


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
    class NeverCalled(BatchExecutionModule):
        async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
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
    assert agent.mode == "chat"
    assert agent.tools == []
    assert agent.output_schema is BatchExtractionOutput


def test_semantic_evidence_validator_handles_equivalent_verbatim_quote(
    monkeypatch,
) -> None:
    from app.schemas.ingestion.grounding import Evidence, PropertyFact
    from app.schemas.ingestion.semantic_patch import SemanticGraphNode

    # Model returns extraction with slight formatting error in evidence
    class SemanticFakeModel(FakeBatchModel):
        async def extract(self, payload, _context) -> BatchExtractionOutput:
            self.calls.append("extract")
            return BatchExtractionOutput(
                scope_keys=["training"],
                extraction=SemanticGraphPatchFragment.model_construct(
                    nodes=[
                        SemanticGraphNode(
                            temp_id="n1",
                            class_name="location",
                            properties=[
                                PropertyFact(
                                    property_name="address",
                                    value="Xa La",
                                    evidence=[
                                        Evidence(
                                            chunk_index=0,
                                            text="Cơ sở 3 — Xa La. Địa điểm: Sân chơi",
                                        )
                                    ],
                                )
                            ],
                        )
                    ],
                    edges=[],
                    coverage=[],
                    warnings=[],
                ),
            )

        async def validate_evidence(self, _payload, _context):
            self.calls.append("validate_evidence")
            return batch_execution.SemanticEvidenceValidationResult(
                decisions=[
                    batch_execution.SemanticEvidenceDecision(
                        issue_id="0:0:nodes.0.properties.0.evidence.0.text",
                        verdict=batch_execution.EvidenceVerdict.EQUIVALENT,
                        source_quote="Cơ sở 3 — Xa La\n\nĐịa điểm:\nSân chơi",
                        explanation="Format difference but exactly supported",
                    )
                ]
            )

    model = SemanticFakeModel()
    module = BatchExecutionModule(model, prompt_max_bytes=1)
    prep = _prepared(["training"])
    prep.payload["batch"]["chunks"] = [
        {"chunkIndex": 0, "text": "Cơ sở 3 — Xa La\n\nĐịa điểm:\nSân chơi"}
    ]

    submit_calls = 0

    async def fake_prepare(*_args):
        return prep

    async def fake_reconcile(_prepared, selected, _extraction):
        return selected

    async def fake_submit(_prepared, scopes, extraction, _context):
        nonlocal submit_calls
        submit_calls += 1
        # First call fails on EVIDENCE_NOT_GROUNDED
        if submit_calls == 1:
            return (
                {
                    "success": False,
                    "stage": "repair_required",
                    "submittedBatchIndex": 0,
                    "scopeKeys": scopes,
                    "errors": [
                        {
                            "code": "EVIDENCE_NOT_GROUNDED",
                            "location": "nodes.0.properties.0.evidence.0.text",
                            "message": "not grounded",
                        }
                    ],
                },
                object(),
            )
        # Second call after EQUIVALENT replacement succeeds
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

    async def fake_load_scope(*_args):
        return {
            "scope": {
                "scopeKeys": ["training"],
                "entityTypes": [],
                "properties": [],
                "relationships": [],
            }
        }

    monkeypatch.setattr(module, "_prepare", fake_prepare)
    monkeypatch.setattr(module, "_reconcile_scope_keys", fake_reconcile)
    monkeypatch.setattr(module, "_submit", fake_submit)
    monkeypatch.setattr(batch_execution.operations, "load_scope", fake_load_scope)
    monkeypatch.setattr(
        batch_execution, "log_ingestion_event", lambda *_args, **_kwargs: None
    )

    result = asyncio.run(module.execute("ing-1", 0, SimpleNamespace(state={})))

    assert result["success"] is True
    assert model.calls == ["extract", "validate_evidence"]
    assert submit_calls == 2


def test_semantic_evidence_validator_handles_unsupported_terminal(monkeypatch) -> None:
    class UnsupportedFakeModel(FakeBatchModel):
        async def validate_evidence(self, _payload, _context):
            self.calls.append("validate_evidence")
            return batch_execution.SemanticEvidenceValidationResult(
                decisions=[
                    batch_execution.SemanticEvidenceDecision(
                        issue_id="0:0:nodes.0.properties.0.evidence.0.text",
                        verdict=batch_execution.EvidenceVerdict.UNSUPPORTED,
                        source_quote=None,
                        explanation="Claim not supported by chunk",
                    )
                ]
            )

    model = UnsupportedFakeModel()
    module = BatchExecutionModule(model, prompt_max_bytes=1)
    prep = _prepared(["training"])
    prep.payload["batch"]["chunks"] = [{"chunkIndex": 0, "text": "Something else"}]

    async def fake_prepare(*_args):
        return prep

    async def fake_reconcile(_prepared, selected, _extraction):
        return selected

    async def fake_submit(_prepared, scopes, _extraction, _context):
        return (
            {
                "success": False,
                "stage": "repair_required",
                "submittedBatchIndex": 0,
                "scopeKeys": scopes,
                "errors": [
                    {
                        "code": "EVIDENCE_NOT_GROUNDED",
                        "location": "nodes.0.properties.0.evidence.0.text",
                        "message": "not grounded",
                    }
                ],
            },
            object(),
        )

    async def fake_load_scope(*_args):
        return {
            "scope": {
                "scopeKeys": ["training"],
                "entityTypes": [],
                "properties": [],
                "relationships": [],
            }
        }

    monkeypatch.setattr(module, "_prepare", fake_prepare)
    monkeypatch.setattr(module, "_reconcile_scope_keys", fake_reconcile)
    monkeypatch.setattr(module, "_submit", fake_submit)
    monkeypatch.setattr(batch_execution.operations, "load_scope", fake_load_scope)
    monkeypatch.setattr(
        batch_execution, "log_ingestion_event", lambda *_args, **_kwargs: None
    )

    result = asyncio.run(module.execute("ing-1", 0, SimpleNamespace(state={})))

    assert result["success"] is False
    assert result["terminal"] is True
    assert result["errors"][0]["code"] == "EVIDENCE_NOT_GROUNDED_UNSUPPORTED"
    assert model.calls == ["extract", "validate_evidence"]
