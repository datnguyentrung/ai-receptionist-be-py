import json
import asyncio
from typing import get_type_hints
from pathlib import Path

from app.agent.agent import root_agent
from app.agent.tools.ingestion_tools import submit_ingestion_batch
from app.core.config import Settings
from app.core.schemas.ingestion import DocumentChunk
from app.schemas.ingestion_schema import (
    OntologyProjection,
    SemanticGraphPatchFragment,
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
            "coverage": [
                {"chunkIndex": 0, "decision": "MAPPED", "reason": "person"}
            ],
        }
    )


def test_llm_schema_has_no_identity() -> None:
    schema = SemanticGraphPatchFragment.model_json_schema()
    assert "identity" not in json.dumps(schema)
    tool_fragment_type = get_type_hints(submit_ingestion_batch)["graph_fragment"]
    assert tool_fragment_type is SemanticGraphPatchFragment


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


def test_repair_cannot_drop_unrelated_valid_fact() -> None:
    old = _semantic(
        [("name", "Phùng Thế Lịch"), ("address", "Văn Quán"), ("phone", "0900")]
    )
    new = _semantic([("name", "Phùng Thế Lịch"), ("phone", "0900")])
    issues = RepairGuard.compare(
        previous_semantic_fragment=old,
        new_semantic_fragment=new,
        previous_validation_issues=[
            {
                "code": "EVIDENCE_NOT_GROUNDED",
                "location": "nodes.0.properties.2.evidence.0.text",
            }
        ],
    )
    assert [issue.code for issue in issues] == ["REPAIR_DROPPED_VALID_FACT"]


def test_evidence_only_repair_cannot_mutate_edge_endpoint() -> None:
    old = SemanticGraphPatchFragment.model_validate(
        {
            "ontologyVersion": "v1",
            "nodes": [],
            "edges": [
                {
                    "edgeName": "knows",
                    "sourceTempId": "entity:abc",
                    "targetTempId": "entity:def",
                    "evidence": [
                        {"source": "people.md", "chunkIndex": 0, "text": "abc def"}
                    ],
                }
            ],
            "coverage": [
                {"chunkIndex": 0, "decision": "MAPPED", "reason": "relationship"}
            ],
        }
    )
    new = SemanticGraphPatchFragment.model_validate(
        {
            "ontologyVersion": "v1",
            "nodes": [],
            "edges": [
                {
                    "edgeName": "knows",
                    "sourceTempId": "abc",
                    "targetTempId": "def",
                    "evidence": [
                        {"source": "people.md", "chunkIndex": 0, "text": "abc def"}
                    ],
                }
            ],
            "coverage": [
                {"chunkIndex": 0, "decision": "MAPPED", "reason": "relationship"}
            ],
        }
    )

    issues = RepairGuard.compare(
        previous_semantic_fragment=old,
        new_semantic_fragment=new,
        previous_validation_issues=[
            {
                "code": "EVIDENCE_NOT_GROUNDED",
                "location": "edges.0.evidence.0.text",
            }
        ],
    )

    assert [issue.code for issue in issues] == ["REPAIR_MUTATED_UNRELATED_EDGE"]


def test_evidence_only_repair_allows_evidence_text_change() -> None:
    old = _semantic([("name", "Phùng Thế Lịch")])
    new_node = old.nodes[0].model_copy(
        update={
            "properties": [
                old.nodes[0].properties[0].model_copy(
                    update={
                        "evidence": [
                            old.nodes[0].properties[0].evidence[0].model_copy(
                                update={"text": "Phùng Thế Lịch"}
                            )
                        ]
                    }
                )
            ]
        }
    )
    new = old.model_copy(update={"nodes": [new_node]})

    issues = RepairGuard.compare(
        previous_semantic_fragment=old,
        new_semantic_fragment=new,
        previous_validation_issues=[
            {
                "code": "EVIDENCE_NOT_GROUNDED",
                "location": "nodes.0.properties.0.evidence.0.text",
            }
        ],
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
            "coverage": [
                {"chunkIndex": 1, "decision": "MAPPED", "reason": "relationship"}
            ],
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
            "coverage": [
                {"chunkIndex": 0, "decision": "MAPPED", "reason": "person"}
            ],
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


def test_retry_limit_becomes_terminal_on_third_failed_submit() -> None:
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
            for _ in range(3)
        ]

    results = asyncio.run(scenario())
    assert [item["stage"] for item in results] == [
        "repair_required",
        "repair_required",
        "explicit_extraction_failure",
    ]
    assert results[-1]["terminal"] is True
    assert results[-1]["nextAction"] is None
    assert results[-1]["errors"][0]["code"] == (
        "BATCH_VALIDATION_RETRY_LIMIT_EXCEEDED"
    )


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


def test_default_ingestion_batch_size_is_10() -> None:
    assert Settings.model_fields["INGESTION_BATCH_SIZE"].default == 10
