import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.core.schemas.ingestion import DocumentChunk
from app.schemas.ingestion_schema import (
    ChunkCoverage,
    Evidence,
    GraphEdge,
    GraphNode,
    GraphPatchFragment,
    IngestionJobStatus,
    OntologyProjection,
    PreparedChunk,
    PropertyFact,
    SemanticGraphPatchFragment,
)
from app.services.ingestion.fact_references import (
    add_local_fact_aliases,
    build_fact_index,
)
from app.services.ingestion.ontology import validate_coverage_integrity
from app.services.ingestion.operations import (
    batch_failure_payload,
    fill,
    finalize,
    get_batch,
)
from app.services.ingestion.readiness import WorkspaceReadinessValidator
from app.services.ingestion.repair_guard import RepairGuard
from app.services.ingestion.repository import IngestionRepository

ONTOLOGY_ID = "00000000-0000-0000-0000-000000000099"


def projection(*, phone_multi_value: bool = False) -> OntologyProjection:
    return OntologyProjection.model_validate({
        "versionId": ONTOLOGY_ID,
        "version": "v1",
        "digest": "semantic-guards",
        "scopeKey": "core",
        "scopeKeys": ["core"],
        "entityTypes": [
            {"technicalName": "person", "identityStrategy": {"required": ["name"]}},
            {"technicalName": "organization", "identityStrategy": {"required": ["name"]}},
            {"technicalName": "location", "identityStrategy": {"required": ["name"]}},
            {"technicalName": "schedule", "identityStrategy": {"required": ["name"]}},
        ],
        "properties": [
            {"entityType": entity, "technicalName": "name", "dataType": "STRING", "required": True, "multiValue": False}
            for entity in ("person", "organization", "location", "schedule")
        ] + [
            {"entityType": "person", "technicalName": "phone", "dataType": "STRING", "multiValue": phone_multi_value}
        ],
        "relationships": [
            {"technicalName": "located_at", "sourceEntityType": "organization", "targetEntityType": "location"}
        ],
        "aliases": [],
    })


def evidence(index: int, text: str) -> list[Evidence]:
    return [Evidence(source="doc.md", chunk_index=index, text=text)]


def fact(name: str, value, index: int, quote: str) -> PropertyFact:
    return PropertyFact(property_name=name, value=value, evidence=evidence(index, quote))


def test_repair_preserves_edges_while_fixing_unrelated_schedule() -> None:
    org = GraphNode(temp_id="org", class_name="organization", identity={"name": "Org"}, properties=[fact("name", "Org", 0, "Org")])
    locations = [
        GraphNode(temp_id=f"loc-{index}", class_name="location", identity={"name": f"Cơ sở {index}"}, properties=[fact("name", f"Cơ sở {index}", 0, f"Cơ sở {index}")])
        for index in range(2, 7)
    ]
    edges = [
        GraphEdge(edge_name="located_at", source_temp_id="org", target_temp_id=node.temp_id, evidence=evidence(0, node.identity["name"]))
        for node in locations
    ]
    baseline = GraphPatchFragment(ontology_version="v1", nodes=[org, *locations], edges=edges, coverage=[])
    repaired = baseline.model_copy(deep=True)
    repaired.nodes.append(GraphNode(temp_id="schedule-6", class_name="schedule", identity={"name": "Lịch cơ sở 6"}, properties=[fact("name", "Lịch cơ sở 6", 0, "Lịch cơ sở 6")]))

    assert RepairGuard.compare(previous_canonical_fragment=baseline, new_canonical_fragment=repaired) == []

    broken = repaired.model_copy(deep=True, update={"edges": repaired.edges[:1]})
    issues = RepairGuard.compare(previous_canonical_fragment=baseline, new_canonical_fragment=broken)
    assert sum(issue.code == "REPAIR_DROPPED_VALID_EDGE" for issue in issues) == 4


def test_repair_cannot_replace_valid_phone_and_baseline_never_shrinks() -> None:
    original = GraphPatchFragment(
        ontology_version="v1",
        nodes=[GraphNode(temp_id="person", class_name="person", identity={"name": "HLV"}, properties=[fact("name", "HLV", 0, "HLV"), fact("phone", "0369 222 068", 0, "0369 222 068")])],
        edges=[],
        coverage=[],
    )
    changed = original.model_copy(deep=True)
    changed.nodes[0].properties[1].value = "033 999 8191"
    issues = RepairGuard.compare(previous_canonical_fragment=original, new_canonical_fragment=changed)
    assert {issue.code for issue in issues} == {"REPAIR_CHANGED_VALID_FACT"}

    smaller = GraphPatchFragment(ontology_version="v1", nodes=[], edges=[], coverage=[])
    merged = RepairGuard.merge_baselines(original, smaller)
    assert merged is not None
    assert merged.nodes[0].properties[1].value == "0369 222 068"


def test_negative_coverage_cannot_replace_rejected_duplicate_label() -> None:
    chunk = PreparedChunk(chunk_id="c", chunk_index=0, text="Đổi tên thành Văn Quán năm 2019", content_hash="h", token_count=8, source_anchor="doc#0")
    duplicate = GraphPatchFragment(ontology_version="v1", nodes=[], edges=[], coverage=[ChunkCoverage(chunk_index=0, decision="DUPLICATE_EVIDENCE", reason="Already known")])
    no_fact = duplicate.model_copy(update={"coverage": [ChunkCoverage(chunk_index=0, decision="NO_RELEVANT_FACT", reason="Historical note")]})

    assert {issue.code for issue in validate_coverage_integrity(duplicate, [chunk])} == {"DUPLICATE_WITHOUT_MATCHING_FACT"}
    assert {issue.code for issue in validate_coverage_integrity(no_fact, [chunk])} == {"COVERAGE_REVIEW_REQUIRED"}


def test_current_submission_exposes_predictable_local_fact_reference() -> None:
    semantic = SemanticGraphPatchFragment.model_validate({
        "nodes": [{
            "tempId": "org_vquan",
            "className": "organization",
            "properties": [{
                "propertyName": "name",
                "value": "Văn Quán",
                "evidence": [{"chunkIndex": 1, "text": "Văn Quán"}],
            }],
        }],
        "edges": [],
        "coverage": [],
    })
    canonical = GraphPatchFragment(
        ontology_version=ONTOLOGY_ID,
        nodes=[GraphNode(
            temp_id="canonical-org-key",
            class_name="organization",
            identity={"name": "Văn Quán"},
            properties=[fact("name", "Văn Quán", 1, "Văn Quán")],
        )],
        edges=[],
        coverage=semantic.coverage,
    )
    index = build_fact_index([canonical])
    add_local_fact_aliases(index, semantic, canonical)
    assert index["local-property:org_vquan:name"]["value"] == "Văn Quán"


def workspace_with_phones(*, second_phone: str, phone_multi_value: bool = False):
    phone_values = (["0369 222 068"], [second_phone]) if phone_multi_value else ("0369 222 068", second_phone)
    chunks = tuple(
        PreparedChunk(chunk_id=f"c{index}", chunk_index=index, text=f"HLV {value if isinstance(value, str) else value[0]}", content_hash=f"h{index}", token_count=4, source_anchor=f"doc#{index}")
        for index, value in enumerate(phone_values)
    )
    fragments = []
    for index, value in enumerate(phone_values):
        quote = chunks[index].text
        fragments.append(GraphPatchFragment(
            ontology_version=ONTOLOGY_ID,
            nodes=[GraphNode(temp_id="person-key", class_name="person", identity={"name": "HLV"}, properties=[fact("name", "HLV", index, quote), fact("phone", value, index, quote)])],
            edges=[],
            coverage=[ChunkCoverage(chunk_index=index, decision="MAPPED", reason="phone")],
        ))
    batches = tuple(
        SimpleNamespace(
            batch_index=index,
            chunk_indexes=[index],
            status="STAGED",
            scope_keys=["core"],
            merged_schema_hash="d",
            graph_fragment=fragment.model_dump(by_alias=True, mode="json"),
            validated_baseline=None,
        )
        for index, fragment in enumerate(fragments)
    )
    workspace = SimpleNamespace(
        job=SimpleNamespace(id=uuid4(), ontology_version_id=UUID(ONTOLOGY_ID), status=IngestionJobStatus.BATCHING, readiness_fingerprint=None, error_message=None, error_stage=None),
        document=SimpleNamespace(id=uuid4(), name="phones.md", current_version_id=None),
        version=SimpleNamespace(id=uuid4(), ontology_version_id=UUID(ONTOLOGY_ID), ontology_digest="d", content_hash="h"),
        chunks=chunks,
        batches=batches,
    )
    return workspace, projection(phone_multi_value=phone_multi_value)


def test_readiness_blocks_scalar_conflict_and_merges_multivalue() -> None:
    workspace, schema = workspace_with_phones(second_phone="033 999 8191")
    result = WorkspaceReadinessValidator().validate(workspace, schema)
    assert "SCALAR_PROPERTY_CONFLICT" in {issue.code for issue in result.issues}
    assert result.fragment is None

    workspace, schema = workspace_with_phones(second_phone="033 999 8191", phone_multi_value=True)
    result = WorkspaceReadinessValidator().validate(workspace, schema)
    assert result.issues == []
    phone = next(item for item in result.fragment.nodes[0].properties if item.property_name == "phone")
    assert phone.value == ["0369 222 068", "033 999 8191"]


def test_finalize_and_fill_both_block_scalar_conflict() -> None:
    workspace, schema = workspace_with_phones(second_phone="033 999 8191")

    class Repository:
        async def get_workspace(self, _):
            return workspace

        async def mark_batch_for_repair(self, _ingestion_id, batch_index, issues, **_kwargs):
            workspace.batches[batch_index].status = "REPAIR_REQUIRED"
            workspace.batches[batch_index].validation_issues = issues
            workspace.job.status = IngestionJobStatus.BATCHING
            return workspace

        async def mark_ready(self, *_args):
            raise AssertionError("scalar conflict must not become READY")

    class Cache:
        async def get_many(self, *_args):
            return schema

    result = asyncio.run(finalize(Repository(), Cache(), str(workspace.job.id)))
    assert result["stage"] == "semantic_conflict"

    workspace, schema = workspace_with_phones(second_phone="033 999 8191")
    workspace.job.status = IngestionJobStatus.READY

    class Store:
        async def fill(self, *_args):
            raise AssertionError("Neo4j must not be called")

    result = asyncio.run(fill(Repository(), Cache(), Store(), str(workspace.job.id)))
    assert result["stage"] == "semantic_conflict"


def test_get_batch_exposes_protected_baseline_and_current_fact_refs() -> None:
    baseline = GraphPatchFragment(
        ontology_version=ONTOLOGY_ID,
        nodes=[
            GraphNode(
                temp_id="org-key",
                class_name="organization",
                identity={"name": "Văn Quán"},
                properties=[fact("name", "Văn Quán", 1, "Văn Quán")],
            ),
            GraphNode(
                temp_id="person-key",
                class_name="person",
                identity={"name": "Phùng Thế Lịch"},
                properties=[fact("name", "Phùng Thế Lịch", 1, "Phùng Thế Lịch")],
            ),
        ],
        edges=[
            GraphEdge(
                edge_name="founded_by",
                source_temp_id="org-key",
                target_temp_id="person-key",
                evidence=evidence(1, "Phùng Thế Lịch"),
            )
        ],
        coverage=[ChunkCoverage(chunk_index=1, decision="MAPPED", reason="founder")],
    )
    chunk = PreparedChunk(
        chunk_id="c1",
        chunk_index=1,
        text="Văn Quán do Phùng Thế Lịch sáng lập",
        content_hash="h1",
        token_count=8,
        source_anchor="doc#1",
    )
    batch = SimpleNamespace(
        batch_index=0,
        chunk_indexes=[1],
        status="REPAIR_REQUIRED",
        scope_keys=["core"],
        validated_baseline=baseline.model_dump(by_alias=True, mode="json"),
        validation_issues=[
            {"code": "MAPPED_WITHOUT_MAPPING", "location": "coverage.2.decision"}
        ],
    )
    workspace = SimpleNamespace(
        job=SimpleNamespace(
            id=uuid4(),
            ontology_version_id=UUID(ONTOLOGY_ID),
            status=IngestionJobStatus.BATCHING,
            scope_hint=None,
        ),
        document=SimpleNamespace(id=uuid4(), name="history.md"),
        version=SimpleNamespace(id=uuid4()),
        chunks=(chunk,),
        batches=(batch,),
    )

    class Repository:
        async def get_workspace(self, _):
            return workspace

    result = asyncio.run(get_batch(Repository(), str(workspace.job.id), 0))
    assert result["batch"]["chunks"][0]["evidenceUnits"] == [
        {
            "evidenceRef": "chunk:1:line:1",
            "chunkIndex": 1,
            "kind": "LINE",
            "text": "Văn Quán do Phùng Thế Lịch sáng lập",
            "startLine": 1,
            "endLine": 1,
        }
    ]
    context = result["batch"]["repairContext"]
    assert context["mode"] == "ADDITIVE_DELTA"
    assert context["protectedBaseline"]["edges"][0]["evidence"][0]["text"] == "Phùng Thế Lịch"
    assert context["protectedBaseline"]["nodes"][0]["tempId"] == "org-key"
    assert context["baselineFingerprint"]
    assert context["deltaTemplate"]["baselineFingerprint"] == context["baselineFingerprint"]
    assert any(
        item["propertyName"] == "name" and item["value"] == "Văn Quán"
        for item in result["batch"]["canonicalFactContext"]
    )


def test_nonretryable_coverage_review_does_not_request_another_repair() -> None:
    batch = SimpleNamespace(
        batch_index=0,
        chunk_indexes=[0],
        status="REPAIR_REQUIRED",
        scope_keys=["core"],
        validation_attempts=1,
        validation_issues=[],
        validated_baseline=None,
    )
    workspace = SimpleNamespace(
        job=SimpleNamespace(id=uuid4()),
        batches=(batch,),
    )
    issues = [
        {
            "code": "COVERAGE_REVIEW_REQUIRED",
            "message": "Substantive prose requires review",
            "location": "coverage.0.decision",
            "retryable": False,
        }
    ]
    batch.validation_issues = issues
    result = batch_failure_payload(workspace, 0, issues, max_attempts=2)
    assert result["stage"] == "coverage_review_required"
    assert result["retryRequired"] is False
    assert result["nextAction"] == "request_coverage_review"


def test_nonretryable_coverage_review_does_not_consume_retry_budget() -> None:
    async def scenario():
        repository = IngestionRepository()
        workspace, _, _ = await repository.create_or_resume(
            artifact_name="slogan.md",
            content_hash="slogan-hash",
            chunks=[
                DocumentChunk(
                    index=0,
                    source="slogan.md",
                    content="Nơi hội tụ những bạn trẻ yêu thích võ thuật",
                    documentId="doc",
                    chunkId="chunk-0",
                    contentHash="chunk-hash",
                    structuralPath="intro#0",
                    startLine=1,
                    endLine=1,
                )
            ],
            ontology=SimpleNamespace(version_id=ONTOLOGY_ID, version="v1"),
            document_key="slogan",
            scope_hint=None,
            skill_digest="skill",
            model_id="model",
            compiler_version="compiler",
            batch_size=5,
            max_batch_chars=1000,
        )
        issues = [{
            "code": "COVERAGE_REVIEW_REQUIRED",
            "message": "review",
            "location": "coverage.0.decision",
            "retryable": False,
        }]
        for _ in range(4):
            workspace = await repository.store_batch_result(
                str(workspace.job.id),
                0,
                [{"scopeKey": "core", "schemaHash": "d"}],
                "d",
                None,
                None,
                issues,
                max_attempts=2,
                count_attempt=False,
            )
        return workspace

    workspace = asyncio.run(scenario())
    assert workspace.batches[0].validation_attempts == 0
    assert workspace.batches[0].status == "REPAIR_REQUIRED"
    assert workspace.job.status == IngestionJobStatus.BATCHING


def test_begin_after_terminal_failure_creates_fresh_workspace() -> None:
    async def scenario():
        repository = IngestionRepository()
        arguments = {
            "artifact_name": "retry.md",
            "content_hash": "retry-hash",
            "chunks": [
                DocumentChunk(
                    index=0,
                    source="retry.md",
                    content="Organization name",
                    documentId="doc",
                    chunkId="chunk-0",
                    contentHash="chunk-hash",
                    structuralPath="intro#0",
                    startLine=1,
                    endLine=1,
                )
            ],
            "ontology": SimpleNamespace(version_id=ONTOLOGY_ID, version="v1"),
            "document_key": "retry",
            "scope_hint": None,
            "skill_digest": "skill",
            "model_id": "model",
            "compiler_version": "compiler",
            "batch_size": 5,
            "max_batch_chars": 1000,
        }
        failed, _, _ = await repository.create_or_resume(**arguments)
        await repository.mark_failed(
            str(failed.job.id), "batch_validation", "retry limit exceeded"
        )
        fresh, resumed, committed = await repository.create_or_resume(**arguments)
        return failed, fresh, resumed, committed

    failed, fresh, resumed, committed = asyncio.run(scenario())
    assert fresh.job.id != failed.job.id
    assert fresh.version.id != failed.version.id
    assert resumed is False
    assert committed is False
    assert fresh.job.status == IngestionJobStatus.BATCHING


def test_valid_cross_batch_edge_is_kept_in_repair_baseline() -> None:
    fragment = GraphPatchFragment(
        ontology_version=ONTOLOGY_ID,
        nodes=[
            GraphNode(
                temp_id="person-key",
                class_name="person",
                identity={"name": "Phùng Thế Lịch"},
                properties=[fact("name", "Phùng Thế Lịch", 1, "Phùng Thế Lịch")],
            )
        ],
        edges=[
            GraphEdge(
                edge_name="founded_by",
                source_temp_id="external-org-key",
                target_temp_id="person-key",
                evidence=evidence(1, "Phùng Thế Lịch"),
            )
        ],
        coverage=[],
    )
    baseline = RepairGuard.extract_validated_baseline(fragment, [])
    assert baseline is not None
    assert len(baseline.edges) == 1
