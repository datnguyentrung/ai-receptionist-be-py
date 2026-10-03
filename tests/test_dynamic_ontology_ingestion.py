import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.schemas.ingestion import DocumentChunk
from app.models import (
    OntologyEntityType,
    OntologyProperty,
    OntologyRelationship,
    OntologyScope,
    SchemaProposalStatus,
    SchemaProposalType,
)
from app.schemas.ingestion_schema import (
    ChunkCoverage,
    Evidence,
    GraphEdge,
    GraphNode,
    GraphPatchFragment,
    IngestionJobStatus,
    OntologyProjection,
    PreparedChunk,
    SemanticGraphPatchFragment,
)
from app.services.ingestion.graph_store import Neo4jIngestionStore
from app.services.ingestion.ontology import OntologyRegistry, merge_projections
from app.services.ingestion.operations import (
    _classify_missing_scopes,
    finalize,
    submit_batch,
)
from app.services.ingestion.repository import (
    IngestionRepository,
    snapshot_bindings_unchanged,
)
from app.services.ontology_lifecycle import OntologyLifecycle, proposal_review_status


def projection(scope: str, *, entities=None, properties=None, relationships=None):
    return OntologyProjection.model_validate({
        "versionId": "00000000-0000-0000-0000-000000000010",
        "version": "v1", "digest": f"{scope}-hash", "scopeKey": scope,
        "scopeKeys": [scope], "description": scope,
        "entityTypes": entities or [], "properties": properties or [],
        "relationships": relationships or [], "aliases": [],
    })


def test_single_scope_snapshot_is_usable_without_core_fallback() -> None:
    item = projection("competition", entities=[{"id": "e1", "technicalName": "match"}])
    merged = merge_projections([item])
    assert merged.scope_keys == ["competition"]
    assert [entity["technicalName"] for entity in merged.entity_types] == ["match"]


def test_multiple_scopes_merge_and_deduplicate_shared_definitions() -> None:
    person = {"id": "e-person", "technicalName": "person"}
    left = projection("training", entities=[person, {"id": "e-course", "technicalName": "course"}])
    right = projection("finance", entities=[person, {"id": "e-fee", "technicalName": "fee"}])
    merged = merge_projections([left, right])
    assert merged.scope_keys == ["training", "finance"]
    assert {item["technicalName"] for item in merged.entity_types} == {"person", "course", "fee"}


def test_conflicting_snapshot_definitions_fail_instead_of_silently_winning() -> None:
    left = projection("a", entities=[{"id": "same", "technicalName": "person", "description": "A"}])
    right = projection("b", entities=[{"id": "same", "technicalName": "person", "description": "B"}])
    with pytest.raises(RuntimeError, match="Conflicting ontology definitions"):
        merge_projections([left, right])


def test_no_selected_scope_is_rejected_instead_of_defaulting_to_core() -> None:
    with pytest.raises(ValueError, match="No ontology projections"):
        merge_projections([])


@pytest.mark.parametrize(
    ("fragment", "expected_code"),
    [
        ({"nodes": [{"tempId": "n", "className": "new_type"}], "edges": []}, "UNKNOWN_ENTITY_TYPE"),
        ({"nodes": [{"tempId": "n", "className": "course", "properties": [{
            "propertyName": "new_property", "value": "x",
            "evidence": [{"source": "x", "chunkIndex": 0, "text": "x"}],
        }]}], "edges": []}, "UNKNOWN_PROPERTY"),
        ({"nodes": [{"tempId": "a", "className": "course"},
                    {"tempId": "b", "className": "person"}],
          "edges": [{"edgeName": "new_relation", "sourceTempId": "a", "targetTempId": "b",
                     "evidence": [{"source": "x", "chunkIndex": 0, "text": "x"}]}]},
         "UNKNOWN_RELATIONSHIP"),
    ],
)
def test_unknown_schema_is_returned_as_proposal_candidate(fragment, expected_code) -> None:
    ontology_id = uuid4()
    batch = SimpleNamespace(batch_index=0, chunk_indexes=[0], status="PENDING",
                            scope_keys=[], validation_attempts=0)
    workspace = SimpleNamespace(
        job=SimpleNamespace(id=uuid4(), ontology_version_id=ontology_id,
                            status=IngestionJobStatus.BATCHING, error_message=None, error_stage=None),
        document=SimpleNamespace(id=uuid4()),
        version=SimpleNamespace(id=uuid4(), ontology_version_id=ontology_id),
        chunks=(PreparedChunk(chunkId="c", chunkIndex=0, text="x", contentHash="h",
                              tokenCount=1, sourceAnchor="x#0"),), batches=(batch,),
    )
    schema = projection(
        "training",
        entities=[{"id": "course", "technicalName": "course"},
                  {"id": "person", "technicalName": "person"}],
    )

    class Repository:
        async def get_workspace(self, _): return workspace
        async def store_batch_result(self, *args, **kwargs):
            batch.status = "REPAIR_REQUIRED"
            return workspace

    class Cache:
        async def get_many(self, *_): return schema
        async def get(self, *_): return schema

    fragment = {
        "ontologyVersion": "v1", **fragment,
        "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "test"}],
    }
    result = asyncio.run(submit_batch(Repository(), Cache(), "job", 0, ["training"], fragment))
    assert result["stage"] == "schema_gap_candidate"
    assert expected_code in {item["code"] for item in result["errors"]}


def test_scope_proposal_types_and_human_review_transitions() -> None:
    assert SchemaProposalType.NEW_SCOPE.value == "NEW_SCOPE"
    assert SchemaProposalType.MODIFY_SCOPE.value == "MODIFY_SCOPE"
    assert proposal_review_status(SchemaProposalStatus.PROPOSED, True) == SchemaProposalStatus.APPROVED
    assert proposal_review_status(SchemaProposalStatus.PROPOSED, False) == SchemaProposalStatus.REJECTED
    with pytest.raises(ValueError):
        proposal_review_status(SchemaProposalStatus.APPROVED, True)


def test_version_change_preserves_only_unchanged_scope_hashes() -> None:
    bindings = [SimpleNamespace(scope_key="training", snapshot_hash="same")]
    assert snapshot_bindings_unchanged(bindings, {"training": "same"})
    assert not snapshot_bindings_unchanged(bindings, {"training": "changed"})


def test_finalize_rejects_repair_and_blocked_batches_and_accepts_many_staged() -> None:
    async def scenario(statuses):
        schema = projection("training")
        workspace = SimpleNamespace(
            job=SimpleNamespace(id=uuid4(), ontology_version_id=uuid4(), status=IngestionJobStatus.BATCHING,
                                readiness_fingerprint=None, error_message=None, error_stage=None),
            document=SimpleNamespace(id=uuid4(), name="empty.md"),
            version=SimpleNamespace(id=uuid4(), content_hash="h", ontology_version_id=uuid4(), ontology_digest="d"),
            chunks=(),
            batches=tuple(SimpleNamespace(batch_index=i, chunk_indexes=[], status=status, scope_keys=["training"],
                                          merged_schema_hash="h", validated_baseline=None,
                                          graph_fragment={"ontologyVersion": schema.version_id, "nodes": [], "edges": [], "coverage": []})
                          for i, status in enumerate(statuses)),
        )

        class Repository:
            async def get_workspace(self, _): return workspace
            async def mark_ready(self, _, fingerprint):
                workspace.job.status = IngestionJobStatus.READY
                workspace.job.readiness_fingerprint = fingerprint
                return workspace
            async def mark_batch_for_repair(self, *_args): return workspace
        class Cache:
            async def get_many(self, *_args): return schema
        return await finalize(Repository(), Cache(), "job")

    assert asyncio.run(scenario(["REPAIR_REQUIRED"]))["stage"] == "repair_required"
    assert asyncio.run(scenario(["BLOCKED_SCHEMA"]))["stage"] == "awaiting_schema_approval"
    assert asyncio.run(scenario(["STAGED", "STAGED"]))["stage"] == "ready_to_fill"


def test_repository_is_process_local_and_does_not_resume_after_restart() -> None:
    async def scenario() -> None:
        ontology_id = uuid4()
        chunk = DocumentChunk(
            index=0,
            source="local.txt",
            content="process local evidence",
            documentId="doc_1",
            chunkId="chk_1",
            contentHash="chunk-hash",
            structuralPath="local.txt#0",
            startLine=1,
            endLine=1,
        )
        first = IngestionRepository()
        workspace, resumed, committed = await first.create_or_resume(
            artifact_name="local.txt",
            content_hash="content-hash",
            chunks=[chunk],
            ontology=SimpleNamespace(version_id=str(ontology_id), version="v-local"),
            document_key="local-document",
            scope_hint=None,
            skill_digest="skill",
            model_id="model",
            compiler_version="compiler",
            batch_size=10,
            max_batch_chars=15000,
        )
        assert not resumed and not committed
        assert await first.get_workspace(str(workspace.job.id)) is workspace

        restarted = IngestionRepository()
        assert await restarted.get_workspace(str(workspace.job.id)) is None

    asyncio.run(scenario())


def test_submit_has_no_neo4j_staging_primitive() -> None:
    assert not hasattr(Neo4jIngestionStore, "stage_batch")


@pytest.mark.parametrize(
    ("kind", "payload", "expected_type"),
    [
        (SchemaProposalType.NEW_SCOPE,
         {"scopeKey": "competition", "description": "Giải đấu",
          "entityTypeTechnicalNames": ["person"]}, OntologyScope),
        (SchemaProposalType.NEW_ENTITY_TYPE,
         {"technicalName": "match", "scopeKeys": ["competition"]}, OntologyEntityType),
        (SchemaProposalType.NEW_PROPERTY,
         {"entityType": "person", "technicalName": "weight", "dataType": "FLOAT"},
         OntologyProperty),
        (SchemaProposalType.NEW_RELATIONSHIP,
         {"technicalName": "competes_in", "sourceEntityType": "person",
          "targetEntityType": "match"}, OntologyRelationship),
    ],
)
def test_approved_new_schema_changes_have_deterministic_apply_primitives(
    monkeypatch, kind, payload, expected_type
) -> None:
    version_id = uuid4()

    class Session:
        def __init__(self): self.added = []
        def add(self, item): self.added.append(item)
        def add_all(self, items): self.added.extend(items)
        async def flush(self):
            for item in self.added:
                if hasattr(item, "id") and item.id is None:
                    item.id = uuid4()

    lifecycle = OntologyLifecycle(None, None, None)
    session = Session()

    async def entities(_session, _version, names):
        return [SimpleNamespace(id=uuid4(), technical_name=name) for name in names]

    async def scope(_session, _version, key):
        return SimpleNamespace(id=uuid4(), scope_key=key)

    monkeypatch.setattr(lifecycle, "_entities_by_names", entities)
    monkeypatch.setattr(lifecycle, "_scope_by_key", scope)
    proposal = SimpleNamespace(
        proposal_type=kind, payload=payload, affected_scope_keys=payload.get("scopeKeys", [])
    )
    asyncio.run(lifecycle._apply_change(session, proposal, version_id, {}))
    assert any(isinstance(item, expected_type) for item in session.added)


def test_skill_owns_batch_loop_scope_selection_and_approval_gate() -> None:
    skill = (Path(__file__).parents[1] / "app/agent/skills/ingestion/SKILL.md").read_text(encoding="utf-8")
    assert "list_ontology_scopes" in skill
    assert "một hoặc nhiều scope" in skill
    assert "không tự approve" in skill
    assert "Chỉ sau trạng thái `APPROVED`" in skill


def test_domain_range_mismatch_uses_dynamic_ontology_compatibility() -> None:
    selected = projection(
        "training",
        entities=[
            {"id": "org", "technicalName": "organization"},
            {"id": "program", "technicalName": "class_program"},
            {"id": "policy", "technicalName": "policy"},
        ],
        relationships=[{
            "id": "has-policy",
            "technicalName": "has_policy",
            "sourceEntityType": "class_program",
            "targetEntityType": "policy",
        }],
    )
    other = projection(
        "organization",
        entities=[
            {"id": "org", "technicalName": "organization"},
            {"id": "policy", "technicalName": "policy"},
        ],
        relationships=[{
            "id": "org-policy",
            "technicalName": "organization_has_policy",
            "sourceEntityType": "organization",
            "targetEntityType": "policy",
        }],
    )
    semantic = SemanticGraphPatchFragment.model_validate({
        "ontologyVersion": "v1",
        "nodes": [
            {"tempId": "org-1", "className": "organization"},
            {"tempId": "policy-1", "className": "policy"},
        ],
        "edges": [{
            "edgeName": "has_policy",
            "sourceTempId": "org-1",
            "targetTempId": "policy-1",
            "evidence": [{"source": "x", "chunkIndex": 0, "text": "x"}],
        }],
        "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "test"}],
    })
    issues = [{
        "code": "RELATIONSHIP_DOMAIN_RANGE_MISMATCH",
        "message": "has_policy expects class_program -> policy",
        "location": "edges.0",
        "retryable": False,
    }]

    class Cache:
        async def list_scopes(self, _):
            return (
                SimpleNamespace(scope_key="training"),
                SimpleNamespace(scope_key="organization"),
            )

        async def get_many(self, scope_keys, _):
            return other if scope_keys == ["organization"] else selected

        async def get(self, scope_key, _):
            return other if scope_key == "organization" else selected

    result = asyncio.run(_classify_missing_scopes(
        Cache(),
        selected.version_id,
        selected,
        semantic,
        issues,
    ))

    assert result[0]["code"] == "MISSING_SCOPE"
    assert result[0]["candidateScopes"] == ["organization"]
    assert result[0]["candidateRelationships"] == {
        "organization": ["organization_has_policy"]
    }


def test_polymorphic_relationships_validation() -> None:
    proj = projection(
        "core",
        entities=[
            {"id": "e1", "technicalName": "class_program"},
            {"id": "e2", "technicalName": "location"},
            {"id": "e3", "technicalName": "schedule"},
        ],
        relationships=[
            {
                "id": "r1",
                "technicalName": "has_schedule",
                "sourceEntityType": "class_program",
                "targetEntityType": "schedule",
            },
            {
                "id": "r2",
                "technicalName": "has_schedule",
                "sourceEntityType": "location",
                "targetEntityType": "schedule",
            },
        ],
    )
    registry = OntologyRegistry(proj)

    # Test edge from location -> schedule (must be valid)
    fragment = GraphPatchFragment(
        ontology_version="v1",
        nodes=[
            GraphNode(class_name="location", temp_id="loc_1", properties=[], identity={"name": "Cơ sở 1"}),
            GraphNode(class_name="schedule", temp_id="sched_1", properties=[], identity={"name": "Lịch 1"}),
        ],
        edges=[
            GraphEdge(edge_name="has_schedule", source_temp_id="loc_1", target_temp_id="sched_1", properties={}, evidence=[Evidence(source="test", chunk_index=0, text="test")]),
        ],
        coverage=[ChunkCoverage(chunk_index=0, decision="MAPPED", reason="test")],
    )
    issues = registry.validate_fragment(fragment, [PreparedChunk(chunk_id="c0", chunk_index=0, text="test", content_hash="h", token_count=1, source_anchor="a")])
    domain_issues = [i for i in issues if i.code == "RELATIONSHIP_DOMAIN_RANGE_MISMATCH"]
    assert len(domain_issues) == 0, f"Expected 0 mismatch issues, got {domain_issues}"



def test_mapped_coverage_requires_real_graph_contribution() -> None:
    registry = OntologyRegistry(projection("core"))
    chunk = PreparedChunk(
        chunkId="c0",
        chunkIndex=0,
        text="Năm 2017 đạt thành tích nổi bật.",
        contentHash="h",
        tokenCount=8,
        sourceAnchor="source#0",
    )
    fragment = GraphPatchFragment(
        ontologyVersion="v1",
        nodes=[],
        edges=[],
        coverage=[
            ChunkCoverage(
                chunkIndex=0,
                decision="MAPPED",
                reason="Thành tích năm 2017",
            )
        ],
    )

    issues = registry.validate_fragment(fragment, [chunk])

    assert "MAPPED_WITHOUT_MAPPING" in {issue.code for issue in issues}


def test_schema_gap_coverage_becomes_schema_gap_candidate() -> None:
    registry = OntologyRegistry(projection("core"))
    chunk = PreparedChunk(
        chunkId="c0",
        chunkIndex=0,
        text="Phùng Thế Lịch là người sáng lập hệ thống.",
        contentHash="h",
        tokenCount=10,
        sourceAnchor="source#0",
    )
    fragment = GraphPatchFragment(
        ontologyVersion="v1",
        nodes=[],
        edges=[],
        coverage=[
            ChunkCoverage(
                chunkIndex=0,
                decision="SCHEMA_GAP",
                reason="Ontology chưa có quan hệ founder_of",
            )
        ],
    )

    issues = registry.validate_fragment(fragment, [chunk])
    schema_issues = [issue for issue in issues if issue.code == "SCHEMA_GAP_CANDIDATE"]
    assert len(schema_issues) == 1
    assert schema_issues[0].retryable is False
    assert "founder_of" in schema_issues[0].message


def test_submit_does_not_stage_mapped_batch_without_mapping() -> None:
    async def scenario():
        repository = IngestionRepository()
        ontology_id = uuid4()
        source_chunk = DocumentChunk(
            index=0,
            source="history.md",
            content="Năm 2017 đạt thành tích nổi bật.",
            documentId="doc",
            chunkId="chunk-0",
            contentHash="chunk-hash",
            structuralPath="history#0",
            startLine=1,
            endLine=1,
        )
        workspace, _, _ = await repository.create_or_resume(
            artifact_name="history.md",
            content_hash="content-hash",
            chunks=[source_chunk],
            ontology=SimpleNamespace(version_id=str(ontology_id), version="v1"),
            document_key="history",
            scope_hint=None,
            skill_digest="skill",
            model_id="model",
            compiler_version="compiler",
            batch_size=5,
            max_batch_chars=15000,
        )
        schema = projection("core")

        class Cache:
            async def get_many(self, *_):
                return schema

            async def get(self, *_):
                return schema

        result = await submit_batch(
            repository,
            Cache(),
            str(workspace.job.id),
            0,
            ["core"],
            {
                "ontologyVersion": "v1",
                "nodes": [],
                "edges": [],
                "coverage": [
                    {
                        "chunkIndex": 0,
                        "decision": "MAPPED",
                        "reason": "Thành tích năm 2017",
                    }
                ],
            },
        )
        current = await repository.get_workspace(str(workspace.job.id))
        return result, current

    result, current = asyncio.run(scenario())

    assert result["success"] is False
    assert result["stage"] == "repair_required"
    assert "MAPPED_WITHOUT_MAPPING" in {
        item["code"] for item in result["errors"]
    }
    assert current.batches[0].status == "REPAIR_REQUIRED"


def test_finalize_reopens_legacy_staged_batch_with_fake_mapped_coverage() -> None:
    async def scenario():
        repository = IngestionRepository()
        ontology_id = uuid4()
        source_chunk = DocumentChunk(
            index=0,
            source="history.md",
            content="Năm 2017 đạt thành tích nổi bật.",
            documentId="doc",
            chunkId="chunk-legacy",
            contentHash="legacy-hash",
            structuralPath="history#legacy",
            startLine=1,
            endLine=1,
        )
        workspace, _, _ = await repository.create_or_resume(
            artifact_name="history.md",
            content_hash="legacy-content-hash",
            chunks=[source_chunk],
            ontology=SimpleNamespace(version_id=str(ontology_id), version="v1"),
            document_key="legacy-history",
            scope_hint=None,
            skill_digest="skill",
            model_id="model",
            compiler_version="compiler",
            batch_size=5,
            max_batch_chars=15000,
        )
        batch = workspace.batches[0]
        batch.status = "STAGED"
        batch.scope_keys = ["core"]
        batch.merged_schema_hash = "hash"
        batch.graph_fragment = {
            "ontologyVersion": "v1",
            "nodes": [],
            "edges": [],
            "coverage": [
                {
                    "chunkIndex": 0,
                    "decision": "MAPPED",
                    "reason": "Thành tích năm 2017",
                }
            ],
        }

        class Cache:
            async def get_many(self, *_args):
                return projection("core")

        result = await finalize(repository, Cache(), str(workspace.job.id))
        current = await repository.get_workspace(str(workspace.job.id))
        return result, current

    result, current = asyncio.run(scenario())

    assert result["success"] is False
    assert result["stage"] == "repair_required"
    assert result["repairBatchIndexes"] == [0]
    assert "MAPPED_WITHOUT_MAPPING" in {
        item["code"] for item in result["errors"]
    }
    assert current.batches[0].status == "REPAIR_REQUIRED"
