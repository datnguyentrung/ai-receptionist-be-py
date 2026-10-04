import asyncio
import json
from pathlib import Path
from typing import get_type_hints

from app.agent.agent import root_agent
from app.agent.tools.ingestion_tools import (
    repair_ingestion_batch,
    submit_ingestion_batch,
)
from app.core.config import Settings
from app.core.schemas.ingestion import DocumentChunk
from app.schemas.ingestion_schema import (
    GraphPatchFragment,
    OntologyProjection,
    SemanticBatchExtraction,
    SemanticGraphPatchFragment,
    SemanticGraphRepairDelta,
)
from app.services.ingestion.graph_patch_compiler import GraphPatchCompiler
from app.services.ingestion.graph_store import _merge_fragments
from app.services.ingestion.ontology import OntologyRegistry
from app.services.ingestion.operations import submit_batch
from app.services.ingestion.repair_guard import RepairGuard
from app.services.ingestion.repository import IngestionRepository, stable_entity_key
from app.services.ingestion.workspace.staged_ingestion import IngestionWorkspaceService


def _projection(*, identity_field: str = "name") -> OntologyProjection:
    return OntologyProjection.model_validate(
        {
            "versionId": "ontology-id",
            "version": "v1",
            "digest": "digest",
            "scopeKey": "people",
            "scopeKeys": ["people"],
            "entityTypes": [
                {
                    "id": "person-id",
                    "technicalName": "person",
                    "identityStrategy": {"required": [identity_field]},
                }
            ],
            "properties": [
                {
                    "id": f"property-{name}",
                    "entityType": "person",
                    "technicalName": name,
                    "dataType": "STRING",
                }
                for name in ("name", "member_code", "address", "phone", "role")
            ],
            "relationships": [
                {
                    "id": "rel-knows",
                    "technicalName": "knows",
                    "sourceEntityType": "person",
                    "targetEntityType": "person",
                }
            ],
            "aliases": [],
        }
    )


def _semantic(properties: list[tuple[str, str]]) -> SemanticGraphPatchFragment:
    return SemanticGraphPatchFragment.model_validate(
        {
            "ontologyVersion": "v1",
            "nodes": [
                {
                    "tempId": "person-1",
                    "className": "person",
                    "properties": [
                        {
                            "propertyName": name,
                            "value": value,
                            "evidence": [
                                {
                                    "source": "people.md",
                                    "chunkIndex": 0,
                                    "text": value,
                                }
                            ],
                        }
                        for name, value in properties
                    ],
                }
            ],
            "edges": [],
            "coverage": [],
        }
    )


def test_llm_schema_has_no_identity() -> None:
    schema = SemanticBatchExtraction.model_json_schema()
    assert "identity" not in json.dumps(schema)
    tool_extraction_type = get_type_hints(submit_ingestion_batch)["extraction"]
    assert tool_extraction_type is SemanticBatchExtraction
    repair_delta_type = get_type_hints(repair_ingestion_batch)["repair_delta"]
    assert repair_delta_type is SemanticGraphRepairDelta


def test_identity_is_compiled_from_ontology() -> None:
    result = GraphPatchCompiler().compile(
        _semantic([("name", "  Phùng Thế Lịch  ")]), _projection()
    )
    assert result.issues == []
    assert result.fragment.nodes[0].identity == {"name": "Phùng Thế Lịch"}


def test_identity_never_falls_back_to_name_or_phone() -> None:
    result = GraphPatchCompiler().compile(
        _semantic([("name", "Phùng Thế Lịch"), ("phone", "0900")]),
        _projection(identity_field="member_code"),
    )
    assert result.fragment is None
    assert [issue.code for issue in result.issues] == [
        "IDENTITY_SOURCE_PROPERTY_MISSING"
    ]


def test_casefold_entity_name_is_canonicalized_without_schema_gap() -> None:
    semantic = _semantic([("name", "Phùng Thế Lịch")]).model_copy(
        update={
            "nodes": [
                _semantic([("name", "Phùng Thế Lịch")]).nodes[0].model_copy(
                    update={"class_name": "Person"}
                )
            ]
        }
    )
    canonical = OntologyRegistry(_projection()).canonicalize_semantic_fragment(
        semantic
    )
    assert canonical.nodes[0].class_name == "person"


def _canonical(properties: list[tuple[str, str]]) -> GraphPatchFragment:
    compiled = GraphPatchCompiler().compile(_semantic(properties), _projection())
    return compiled.fragment


def test_repair_cannot_drop_unrelated_valid_fact() -> None:
    old = _canonical(
        [("name", "Phùng Thế Lịch"), ("address", "Văn Quán"), ("phone", "0900")]
    )
    new = _canonical([("name", "Phùng Thế Lịch"), ("phone", "0900")])
    issues = RepairGuard.compare(
        previous_canonical_fragment=old,
        new_canonical_fragment=new,
        previous_validation_issues=[
            {
                "code": "EVIDENCE_NOT_GROUNDED",
                "location": "nodes.0.properties.2.evidence.0.text",
            }
        ],
    )
    assert [issue.code for issue in issues] == ["REPAIR_DROPPED_VALID_FACT"]


def test_repair_guard_allows_adding_properties_on_coverage_repair() -> None:
    old = _canonical([("name", "Phùng Thế Lịch")])
    new = _canonical([("name", "Phùng Thế Lịch"), ("description", "HLV")])
    issues = RepairGuard.compare(
        previous_canonical_fragment=old,
        new_canonical_fragment=new,
        previous_validation_issues=[
            {
                "code": "MAPPED_WITHOUT_MAPPING",
                "location": "coverage.0.decision",
            }
        ],
    )
    assert issues == []


def test_repair_guard_transparent_to_tempid_change_with_same_identity() -> None:
    proj = _projection()
    sem1 = _semantic([("name", "Phùng Thế Lịch"), ("phone", "0900")])
    sem2 = _semantic([("name", "Phùng Thế Lịch"), ("phone", "0900")])
    sem2.nodes[0].temp_id = "completely_different_tempid"
    can1 = GraphPatchCompiler().compile(sem1, proj).fragment
    can2 = GraphPatchCompiler().compile(sem2, proj).fragment

    issues = RepairGuard.compare(
        previous_canonical_fragment=can1,
        new_canonical_fragment=can2,
        previous_validation_issues=[],
    )
    assert issues == []


def test_cross_batch_identity_merge_preserves_distinct_properties() -> None:
    identity = {"name": "Phùng Thế Lịch"}
    first = {
        "nodes": [
            {
                "tempId": "p0",
                "className": "person",
                "identity": identity,
                "properties": [{"propertyName": "phone", "value": "0900"}],
            }
        ],
        "edges": [],
    }
    second = {
        "nodes": [
            {
                "tempId": "p1",
                "className": "person",
                "identity": identity,
                "properties": [{"propertyName": "role", "value": "HLV trưởng"}],
            }
        ],
        "edges": [],
    }
    nodes, _ = _merge_fragments([first, second])
    assert len(nodes) == 1
    assert {item["propertyName"] for item in nodes[0]["properties"]} == {
        "phone",
        "role",
    }


def test_semantic_edge_can_reference_staged_entity_ref() -> None:
    staged_key = stable_entity_key("person", {"name": "Phùng Thế Lịch"})
    semantic = SemanticGraphPatchFragment.model_validate(
        {
            "ontologyVersion": "v1",
            "nodes": [
                {
                    "tempId": "student",
                    "className": "person",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": "Nguyễn Văn A",
                            "evidence": [
                                {
                                    "source": "people.md",
                                    "chunkIndex": 1,
                                    "text": "Nguyễn Văn A",
                                }
                            ],
                        }
                    ],
                }
            ],
            "edges": [
                {
                    "edgeName": "knows",
                    "sourceTempId": f"entity:{staged_key}",
                    "targetTempId": "student",
                    "evidence": [
                        {
                            "source": "people.md",
                            "chunkIndex": 1,
                            "text": "Nguyễn Văn A",
                        }
                    ],
                }
            ],
            "coverage": [],
        }
    )

    result = GraphPatchCompiler().compile(
        semantic,
        _projection(),
        staged_entities={
            f"entity:{staged_key}": {
                "stableKey": staged_key,
                "className": "person",
                "identity": {"name": "Phùng Thế Lịch"},
            }
        },
    )

    assert result.issues == []
    assert result.fragment is not None
    assert result.fragment.edges[0].source_temp_id == staged_key
    assert result.fragment.nodes[0].temp_id == stable_entity_key(
        "person", {"name": "Nguyễn Văn A"}
    )


def test_unknown_staged_entity_ref_returns_validation_issue() -> None:
    semantic = SemanticGraphPatchFragment.model_validate(
        {
            "ontologyVersion": "v1",
            "nodes": [
                {
                    "tempId": "person-1",
                    "className": "person",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": "Nguyễn Văn A",
                            "evidence": [
                                {
                                    "source": "people.md",
                                    "chunkIndex": 0,
                                    "text": "Nguyễn Văn A",
                                }
                            ],
                        }
                    ],
                }
            ],
            "edges": [
                {
                    "edgeName": "knows",
                    "sourceTempId": "entity:missing",
                    "targetTempId": "person-1",
                    "evidence": [
                        {"source": "people.md", "chunkIndex": 0, "text": "Nguyễn Văn A"}
                    ],
                }
            ],
            "coverage": [],
        }
    )

    result = GraphPatchCompiler().compile(semantic, _projection())

    assert [issue.code for issue in result.issues] == ["UNKNOWN_ENTITY_REFERENCE"]


def test_root_has_no_public_ingestion_extractor() -> None:
    assert not any(
        item.name == "ingestion_batch_extractor"
        for item in (root_agent.sub_agents or [])
    )
    assert not (
        Path(__file__).parents[1] / "app/agent/ingestion_extractor_agent.py"
    ).exists()


def test_full_resubmit_is_rejected_after_batch_requires_delta_repair() -> None:
    async def scenario() -> list[dict]:
        repository = IngestionRepository()
        ontology = _projection(identity_field="member_code")
        chunk = DocumentChunk(
            index=0,
            source="people.md",
            content="Phùng Thế Lịch",
            documentId="doc",
            chunkId="chunk",
            contentHash="hash",
            structuralPath="people#0",
            startLine=1,
            endLine=1,
        )
        workspace, _, _ = await repository.create_or_resume(
            artifact_name="people.md",
            content_hash="content-hash",
            chunks=[chunk],
            ontology=type(
                "Active",
                (),
                {
                    "version_id": "00000000-0000-0000-0000-000000000010",
                    "version": "v1",
                },
            )(),
            document_key="people",
            scope_hint=None,
            skill_digest="skill",
            model_id="model",
            compiler_version="compiler",
            batch_size=10,
            max_batch_chars=15000,
        )

        class Cache:
            async def get_many(self, *_):
                return ontology

            async def get(self, *_):
                return ontology

        fragment = _semantic([("name", "Phùng Thế Lịch")])
        return [
            await submit_batch(
                repository,
                Cache(),
                str(workspace.job.id),
                0,
                ["people"],
                fragment,
            )
            for _ in range(4)
        ]

    results = asyncio.run(scenario())
    assert results[0]["stage"] == "extraction_rejected"
    assert results[0]["terminal"] is True
    assert results[0]["nextAction"] == "report_extraction_failure"
    assert results[0]["batchIndex"] == 0
    for forbidden in (
        "graphFragment",
        "semanticFragment",
        "canonicalGraphContext",
        "chunks",
        "snapshotHashes",
        "mergedSchemaHash",
    ):
        assert forbidden not in results[0]

    for res in results[1:]:
        assert res["success"] is False
        assert res["terminal"] is True


def test_runtime_batch_size_partitions_23_chunks_into_10_10_3() -> None:
    chunks = [
        DocumentChunk(
            index=index,
            source="doc.md",
            content=f"chunk {index}",
            documentId="doc",
            chunkId=f"chunk-{index}",
            contentHash=f"hash-{index}",
            structuralPath=f"doc#{index}",
            startLine=index + 1,
            endLine=index + 1,
        )
        for index in range(23)
    ]

    batches = IngestionWorkspaceService.partition(
        chunks, max_batch_chunks=10, max_batch_chars=15000
    )

    assert [batch.chunk_indexes for batch in batches] == [
        list(range(10)),
        list(range(10, 20)),
        list(range(20, 23)),
    ]


def test_default_ingestion_batch_size_is_5() -> None:
    assert Settings.model_fields["INGESTION_BATCH_SIZE"].default == 5


def test_successful_submit_returns_compact_next_batch_summary() -> None:
    async def scenario() -> dict:
        repository = IngestionRepository()
        ontology = _projection()
        chunks = [
            DocumentChunk(
                index=0,
                source="people.md",
                content="Phùng Thế Lịch",
                documentId="doc",
                chunkId="chunk-0",
                contentHash="hash-0",
                structuralPath="people#0",
                startLine=1,
                endLine=1,
            ),
            DocumentChunk(
                index=1,
                source="people.md",
                content="Nguyễn Văn A",
                documentId="doc",
                chunkId="chunk-1",
                contentHash="hash-1",
                structuralPath="people#1",
                startLine=2,
                endLine=2,
            ),
        ]
        workspace, _, _ = await repository.create_or_resume(
            artifact_name="people.md",
            content_hash="content-hash",
            chunks=chunks,
            ontology=type(
                "Active",
                (),
                {
                    "version_id": "00000000-0000-0000-0000-000000000010",
                    "version": "v1",
                },
            )(),
            document_key="people",
            scope_hint=None,
            skill_digest="skill",
            model_id="model",
            compiler_version="compiler",
            batch_size=1,
            max_batch_chars=15000,
        )

        class Cache:
            async def get_many(self, *_):
                return ontology

            async def get(self, *_):
                return ontology
        return await submit_batch(
            repository,
            Cache(),
            str(workspace.job.id),
            0,
            ["people"],
            _semantic([("name", "Phùng Thế Lịch")]),
        )

    result = asyncio.run(scenario())

    assert result["success"] is True
    assert result["stage"] == "batch_staged"
    assert result["nextBatch"] == {
        "batchIndex": 1,
        "chunkIndexes": [1],
        "selectedScopeKeys": [],
    }
    assert "chunks" not in result["nextBatch"]
    assert "canonicalGraphContext" not in result["nextBatch"]
