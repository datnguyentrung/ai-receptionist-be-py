"""Comprehensive unit tests for the updated repair architecture and fault-tolerant mechanisms."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.ingestion.graph_patch import (
    Evidence,
    GraphEdge,
    GraphNode,
    GraphPatchFragment,
    PropertyFact,
)
from app.schemas.ingestion.coverage import ChunkCoverage
from app.schemas.ingestion.semantic_patch import (
    SemanticGraphEdge,
    SemanticGraphNode,
    SemanticGraphRepairDelta,
)
from app.services.ingestion.engine import operations
from app.services.ingestion.engine.operations import (
    _merge_repair_fragment,
    _repair_property_conflicts,
    repair_batch,
)
from app.services.ingestion.schema.repair_guard import RepairGuard


def _dummy_evidence(chunk_index: int = 0) -> Evidence:
    return Evidence(
        source=f"doc.md#0:L1-L10",
        chunk_index=chunk_index,
        text="Sample text",
        section="Intro",
    )


def test_repair_guard_accepts_dropped_temp_ids():
    """Verify that nodes and edges explicitly listed in dropped_temp_ids are not flagged."""
    ev = [_dummy_evidence()]
    node_a = GraphNode(
        temp_id="node_a",
        class_name="location",
        identity={"name": "Hà Đông"},
        properties=[
            PropertyFact(
                property_name="name",
                value="Hà Đông",
                evidence=ev,
            )
        ],
        evidence=ev,
    )
    node_b = GraphNode(
        temp_id="node_b",
        class_name="schedule",
        identity={"name": "Tối 2-4-6"},
        properties=[
            PropertyFact(
                property_name="name",
                value="Tối 2-4-6",
                evidence=ev,
            )
        ],
        evidence=ev,
    )
    edge_ab = GraphEdge(
        edge_name="has_schedule",
        source_temp_id="node_a",
        target_temp_id="node_b",
        properties={},
        evidence=ev,
    )
    baseline = GraphPatchFragment(
        ontology_version="v1",
        nodes=[node_a, node_b],
        edges=[edge_ab],
        coverage=[],
        warnings=[],
    )

    # Candidate after dropping node_b
    candidate = GraphPatchFragment(
        ontology_version="v1",
        nodes=[node_a],
        edges=[],
        coverage=[],
        warnings=[],
    )

    # 1. When dropped_temp_ids is NOT passed, it should flag dropped valid node and edge
    issues_without_drop = RepairGuard.compare(
        previous_canonical_fragment=baseline,
        new_canonical_fragment=candidate,
    )
    assert any(i.code == "REPAIR_DROPPED_VALID_NODE" for i in issues_without_drop)
    assert any(i.code == "REPAIR_DROPPED_VALID_EDGE" for i in issues_without_drop)

    # 2. When dropped_temp_ids contains "node_b", no issues should be raised
    issues_with_drop = RepairGuard.compare(
        previous_canonical_fragment=baseline,
        new_canonical_fragment=candidate,
        dropped_temp_ids=["node_b"],
    )
    assert len(issues_with_drop) == 0


def test_repair_guard_accepts_identity_change_with_same_temp_id():
    """Verify that updating a node's identity does not trigger REPAIR_DROPPED_VALID_NODE."""
    ev = [_dummy_evidence()]
    node_old = GraphNode(
        temp_id="sched_1",
        class_name="schedule",
        identity={"name": "Lịch cũ thứ 2-4-6"},
        properties=[
            PropertyFact(
                property_name="name",
                value="Lịch cũ thứ 2-4-6",
                evidence=ev,
            )
        ],
        evidence=ev,
    )
    baseline = GraphPatchFragment(
        ontology_version="v1",
        nodes=[node_old],
        edges=[],
        coverage=[],
        warnings=[],
    )

    # New node has modified identity, but same temp_id
    node_updated = GraphNode(
        temp_id="sched_1",
        class_name="schedule",
        identity={"name": "Lịch chuẩn Taekwondo Văn Quán"},
        properties=[
            PropertyFact(
                property_name="name",
                value="Lịch chuẩn Taekwondo Văn Quán",
                evidence=ev,
            )
        ],
        evidence=ev,
    )
    candidate = GraphPatchFragment(
        ontology_version="v1",
        nodes=[node_updated],
        edges=[],
        coverage=[],
        warnings=[],
    )

    issues = RepairGuard.compare(
        previous_canonical_fragment=baseline,
        new_canonical_fragment=candidate,
    )
    assert len(issues) == 0


def test_merge_repair_fragment_property_overwrite_and_no_scalar_conflict():
    """Verify property-level overwrite replaces baseline facts without creating scalar conflicts."""
    ev = [_dummy_evidence()]
    node_base = GraphNode(
        temp_id="loc_1",
        class_name="location",
        identity={"name": "Văn Quán"},
        properties=[
            PropertyFact(
                property_name="name",
                value="Văn Quán",
                evidence=ev,
            ),
            PropertyFact(
                property_name="address",
                value="Địa chỉ cũ sai",
                evidence=ev,
            ),
            PropertyFact(
                property_name="phone",
                value="0988888888",
                evidence=ev,
            ),
        ],
        evidence=ev,
    )
    baseline = GraphPatchFragment(
        ontology_version="v1",
        nodes=[node_base],
        edges=[],
        coverage=[],
        warnings=[],
    )

    # Delta updates only address
    node_delta = GraphNode(
        temp_id="loc_1",
        class_name="location",
        identity={"name": "Văn Quán"},
        properties=[
            PropertyFact(
                property_name="address",
                value="Số 10 Phố Văn Quán, Hà Đông, Hà Nội",
                evidence=ev,
            )
        ],
        evidence=ev,
    )
    delta = GraphPatchFragment(
        ontology_version="v1",
        nodes=[node_delta],
        edges=[],
        coverage=[],
        warnings=[],
    )

    merged = _merge_repair_fragment(baseline, delta, None, [])
    assert merged is not None
    assert len(merged.nodes) == 1
    merged_node = merged.nodes[0]

    # Verify preserved phone property
    phone_props = [p for p in merged_node.properties if p.property_name == "phone"]
    assert len(phone_props) == 1
    assert phone_props[0].value == "0988888888"

    # Verify overwritten address property (only 1 value exists, not duplicated)
    addr_props = [p for p in merged_node.properties if p.property_name == "address"]
    assert len(addr_props) == 1
    assert addr_props[0].value == "Số 10 Phố Văn Quán, Hà Đông, Hà Nội"

    # Verify no scalar property conflict
    conflicts = _repair_property_conflicts(merged, projection=None)
    assert len(conflicts) == 0


def test_merge_repair_fragment_removes_dropped_nodes_and_edges():
    """Verify _merge_repair_fragment removes nodes and associated edges listed in dropped_temp_ids."""
    ev = [_dummy_evidence()]
    node_a = GraphNode(
        temp_id="a",
        class_name="location",
        identity={"name": "A"},
        properties=[PropertyFact(property_name="name", value="A", evidence=ev)],
        evidence=ev,
    )
    node_b = GraphNode(
        temp_id="b",
        class_name="schedule",
        identity={"name": "B"},
        properties=[PropertyFact(property_name="name", value="B", evidence=ev)],
        evidence=ev,
    )
    edge_ab = GraphEdge(
        edge_name="has_schedule",
        source_temp_id="a",
        target_temp_id="b",
        properties={},
        evidence=ev,
    )
    baseline = GraphPatchFragment(
        ontology_version="v1",
        nodes=[node_a, node_b],
        edges=[edge_ab],
        coverage=[],
        warnings=[],
    )

    delta = GraphPatchFragment(
        ontology_version="v1",
        nodes=[],
        edges=[],
        coverage=[],
        warnings=[],
    )

    merged = _merge_repair_fragment(
        baseline, delta, None, [], dropped_temp_ids=["b"]
    )
    assert merged is not None
    assert len(merged.nodes) == 1
    assert merged.nodes[0].temp_id == "a"
    assert len(merged.edges) == 0


def test_repair_batch_keeps_validation_failures_explicit(monkeypatch):
    """A lingering validation error must never be staged as successful."""
    async def _run():
        ev = [_dummy_evidence()]
        node_base = GraphNode(
            temp_id="loc_1",
            class_name="location",
            identity={"name": "Văn Quán"},
            properties=[
                PropertyFact(
                    property_name="name",
                    value="Văn Quán",
                    evidence=ev,
                )
            ],
            evidence=ev,
        )
        baseline = GraphPatchFragment(
            ontology_version="v1",
            nodes=[node_base],
            edges=[],
            coverage=[],
            warnings=[],
        )

        # Batch at retry limit: validation_attempts + 1 >= MAX_BATCH_VALIDATION_ATTEMPTS
        batch_mock = MagicMock()
        batch_mock.batch_index = 0
        batch_mock.status = "REPAIR_REQUIRED"
        batch_mock.scope_keys = ["facility"]
        batch_mock.validated_baseline = baseline.model_dump(by_alias=True, mode="json")
        batch_mock.validation_attempts = operations.MAX_BATCH_VALIDATION_ATTEMPTS - 1
        batch_mock.chunk_indexes = [0]
        batch_mock.graph_fragment = None
        batch_mock.validation_issues = []

        workspace_mock = MagicMock()
        workspace_mock.batches = [batch_mock]
        workspace_mock.job.id = "job-test-123"
        workspace_mock.job.ontology_version_id = "ont-v1"
        workspace_mock.chunks = [MagicMock(index=0)]

        repo_mock = MagicMock()
        repo_mock.store_batch_result = AsyncMock()

        # Stub repository required_workspace
        monkeypatch.setattr(
            operations, "required_workspace", AsyncMock(return_value=workspace_mock)
        )

        # Stub ontology cache
        ont_cache_mock = MagicMock()
        proj_mock = MagicMock()
        proj_mock.scope_keys = ["facility"]
        proj_mock.digest = "digest-1"
        proj_mock.entity_types = []
        ont_cache_mock.get_many = AsyncMock(return_value=proj_mock)
        scope_mock = MagicMock(digest="digest-1")
        ont_cache_mock.get = AsyncMock(return_value=scope_mock)
        ont_cache_mock.list_scopes = AsyncMock(return_value=[])

        # Construct repair delta
        delta = SemanticGraphRepairDelta(
            baseline_fingerprint=operations.baseline_fingerprint(baseline),
            repaired_nodes=[],
            repaired_edges=[],
            dropped_temp_ids=[],
            coverage_updates=[],
        )

        # Force compiler or registry to return a validation issue
        class FakeCompileResult:
            issues = [
                MagicMock(
                    model_dump=lambda **kw: {
                        "code": "SYNTAX_OR_SEMANTIC_ISSUE",
                        "message": "Persistent unfixable error in batch",
                        "location": "nodes.0",
                    }
                )
            ]
            fragment = baseline

        monkeypatch.setattr(
            operations.GraphPatchCompiler,
            "compile",
            lambda *args, **kwargs: FakeCompileResult(),
        )

        # Execute repair_batch
        await repair_batch(
            repository=repo_mock,
            ontology_cache=ont_cache_mock,
            ingestion_id="job-test-123",
            batch_index=0,
            scope_keys=["facility"],
            repair_delta=delta,
        )

        # The repository must receive the real issue and decide retry/terminal state.
        repo_mock.store_batch_result.assert_called_once()
        call_args = repo_mock.store_batch_result.call_args[0]

        passed_issues = call_args[6]
        assert "SYNTAX_OR_SEMANTIC_ISSUE" in {
            issue["code"] for issue in passed_issues
        }
        assert passed_issues

    asyncio.run(_run())
