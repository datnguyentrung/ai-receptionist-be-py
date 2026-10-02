import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.models import (
    OntologyEntityType,
    OntologyProperty,
    OntologyRelationship,
    OntologyScope,
    SchemaProposalStatus,
    SchemaProposalType,
)
from app.schemas.ingestion_schema import (
    IngestionJobStatus,
    OntologyProjection,
    PreparedChunk,
)
from app.services.ingestion.graph_store import Neo4jIngestionStore
from app.services.ingestion.ontology import merge_projections
from app.services.ingestion.operations import finalize, submit_batch
from app.core.schemas.ingestion import DocumentChunk
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
        workspace = SimpleNamespace(
            job=SimpleNamespace(id=uuid4(), ontology_version_id=uuid4(), status=IngestionJobStatus.BATCHING,
                                readiness_fingerprint=None, error_message=None, error_stage=None),
            document=SimpleNamespace(id=uuid4()),
            version=SimpleNamespace(id=uuid4(), content_hash="h", ontology_version_id=uuid4()),
            chunks=(),
            batches=tuple(SimpleNamespace(batch_index=i, status=status, scope_keys=["training"],
                                          merged_schema_hash="h", graph_fragment={})
                          for i, status in enumerate(statuses)),
        )

        class Repository:
            async def get_workspace(self, _): return workspace
            async def mark_ready(self, _, fingerprint):
                workspace.job.status = IngestionJobStatus.READY
                workspace.job.readiness_fingerprint = fingerprint
                return workspace
        return await finalize(Repository(), "job")

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
