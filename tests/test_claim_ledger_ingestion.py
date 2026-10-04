"""Tests for First-Pass Batch Ingestion using the Claim Ledger architecture."""

import asyncio
import uuid
from typing import Any

import pytest

from app.agent.tools.ingestion_tools import (
    INGESTION_TOOLS,
    get_service_container,
    repair_ingestion_batch,
    submit_ingestion_batch,
)
from app.core.schemas.ingestion import DocumentChunk
from app.schemas.ingestion_schema import (
    OntologyProjection,
    PreparedChunk,
    PropertyClaimMapping,
    PropertySchemaGap,
    RelationshipSchemaGap,
    SemanticBatchExtraction,
    SemanticClaim,
    SemanticEntity,
)
from app.services.ingestion.batch_claim_compiler import BatchClaimCompiler
from app.services.ingestion.operations import recompile_batch_from_ledger, submit_batch
from app.services.ingestion.repository import IngestionRepository


def _sample_projection() -> OntologyProjection:
    return OntologyProjection.model_validate({
        "versionId": "00000000-0000-0000-0000-000000000001",
        "version": "v1",
        "digest": "digest_v1",
        "scopeKey": "organization",
        "scopeKeys": ["organization"],
        "entityTypes": [
            {
                "id": "e_org",
                "technicalName": "organization",
                "identityStrategy": {"required": ["name"]},
            },
            {
                "id": "e_person",
                "technicalName": "person",
                "identityStrategy": {"required": ["name"]},
            },
        ],
        "properties": [
            {
                "id": "p_org_name",
                "entityType": "organization",
                "technicalName": "name",
                "dataType": "STRING",
            },
            {
                "id": "p_org_founded",
                "entityType": "organization",
                "technicalName": "founded_date",
                "dataType": "STRING",
            },
            {
                "id": "p_person_name",
                "entityType": "person",
                "technicalName": "name",
                "dataType": "STRING",
            },
            {
                "id": "p_person_role",
                "entityType": "person",
                "technicalName": "role",
                "dataType": "STRING",
            },
        ],
        "relationships": [
            {
                "id": "r_founder",
                "technicalName": "founded_by",
                "sourceEntityType": "organization",
                "targetEntityType": "person",
            },
        ],
        "aliases": [],
    })


def _create_sample_chunks() -> list[DocumentChunk]:
    return [
        DocumentChunk(
            index=0,
            source="clb.md",
            content="CLB Taekwondo Vạn Quán thành lập ngày 15/05/2012 bởi Phùng Thế Lịch. Hiện có 6 cơ sở.",
            documentId="doc1",
            chunkId="c0",
            contentHash="h0",
            structuralPath="clb#0",
            startLine=1,
            endLine=3,
        ),
        DocumentChunk(
            index=1,
            source="clb.md",
            content="Lời ngỏ: Chào mừng các bạn đến với võ đường Taekwondo Vạn Quán.",
            documentId="doc1",
            chunkId="c1",
            contentHash="h1",
            structuralPath="clb#1",
            startLine=4,
            endLine=5,
        ),
    ]


class MockOntologyCache:
    def __init__(self, projection: OntologyProjection):
        self.projection = projection

    async def get_many(self, scope_keys: list[str], version_id: str):
        return self.projection

    async def get(self, scope_key: str, version_id: str):
        return self.projection


def test_normal_workflow_excludes_repair_tool():
    """repair_ingestion_batch must not be in the default published INGESTION_TOOLS."""
    tool_names = [tool.__name__ for tool in INGESTION_TOOLS]
    assert "repair_ingestion_batch" not in tool_names
    assert "submit_ingestion_batch" in tool_names
    assert "create_schema_proposal" in tool_names


def test_mixed_chunk_synthesizes_partially_mapped_without_conflict():
    """Mixed chunk with mapped and schema-gap claims synthesizes PARTIALLY_MAPPED without conflict."""
    compiler = BatchClaimCompiler()
    projection = _sample_projection()
    source_chunks = [
        PreparedChunk(
            chunk_id="c0",
            chunk_index=0,
            text="CLB Taekwondo Vạn Quán thành lập ngày 15/05/2012 bởi Phùng Thế Lịch. Hiện có 6 cơ sở.",
            content_hash="h0",
            token_count=20,
            source_anchor="anchor0",
        )
    ]

    extraction = SemanticBatchExtraction(
        entities=[
            SemanticEntity(tempId="org_1", className="organization"),
        ],
        chunks=[
            {
                "chunkIndex": 0,
                "claims": [
                    {
                        "claimId": "c1",
                        "statement": "Tên tổ chức là CLB Taekwondo Vạn Quán",
                        "evidence": {"chunkIndex": 0, "text": "CLB Taekwondo Vạn Quán"},
                        "outcome": "MAPPED",
                        "mapping": {
                            "kind": "PROPERTY",
                            "entityRef": "org_1",
                            "propertyName": "name",
                            "value": "CLB Taekwondo Vạn Quán",
                        },
                    },
                    {
                        "claimId": "c2",
                        "statement": "Thành lập ngày 15/05/2012",
                        "evidence": {"chunkIndex": 0, "text": "thành lập ngày 15/05/2012"},
                        "outcome": "MAPPED",
                        "mapping": {
                            "kind": "PROPERTY",
                            "entityRef": "org_1",
                            "propertyName": "founded_date",
                            "value": "15/05/2012",
                        },
                    },
                    {
                        "claimId": "c3",
                        "statement": "Có 6 cơ sở hoạt động",
                        "evidence": {"chunkIndex": 0, "text": "có 6 cơ sở"},
                        "outcome": "SCHEMA_GAP",
                        "schemaGap": {
                            "kind": "PROPERTY",
                            "entityRef": "org_1",
                            "technicalName": "facility_count",
                            "displayName": "Số lượng cơ sở",
                            "dataType": "INTEGER",
                            "value": 6,
                            "reason": "Ontology thiếu thuộc tính facility_count",
                        },
                    },
                ],
            }
        ],
    )

    result = compiler.compile(
        extraction=extraction,
        ontology_projection=projection,
        source_chunks=source_chunks,
    )

    assert not result.errors
    assert len(result.schema_gaps) == 1
    assert result.schema_gaps[0]["technicalName"] == "facility_count"
    assert result.fragment is not None
    assert len(result.fragment.coverage) == 1
    # Mixed mapped + schema gap -> PARTIALLY_MAPPED
    assert result.fragment.coverage[0].decision == "PARTIALLY_MAPPED"


def test_chunk_with_no_claims_produces_no_relevant_fact_without_heuristic_rejection():
    """A prose chunk with no claims and valid reason must be NO_RELEVANT_FACT, not rejected."""
    compiler = BatchClaimCompiler()
    projection = _sample_projection()
    source_chunks = [
        PreparedChunk(
            chunk_id="c1",
            chunk_index=1,
            text="Lời ngỏ: Chào mừng toàn thể quý vị và các bạn nhỏ đến với võ đường Taekwondo Vạn Quán.",
            content_hash="h1",
            token_count=20,
            source_anchor="anchor1",
        )
    ]

    extraction = SemanticBatchExtraction(
        entities=[],
        chunks=[
            {
                "chunkIndex": 1,
                "claims": [],
                "noRelevantFactReason": "Đoạn văn bản chỉ chứa lời ngỏ chào mừng mở đầu.",
            }
        ],
    )

    result = compiler.compile(
        extraction=extraction,
        ontology_projection=projection,
        source_chunks=source_chunks,
    )

    assert not result.errors
    assert result.fragment is not None
    assert len(result.fragment.coverage) == 1
    assert result.fragment.coverage[0].decision == "NO_RELEVANT_FACT"
    assert "lời ngỏ chào mừng" in result.fragment.coverage[0].reason


def test_batch_fully_supported_stages_first_pass():
    """A batch with mapped claims stages immediately in first pass."""
    async def scenario():
        repo = IngestionRepository()
        projection = _sample_projection()
        chunks = _create_sample_chunks()

        workspace, _, _ = await repo.create_or_resume(
            artifact_name="clb.md",
            content_hash="hash_1",
            chunks=chunks,
            ontology=type("Ont", (), {"version_id": projection.version_id, "version": projection.version})(),
            document_key="clb",
            scope_hint=None,
            skill_digest="skill_v1",
            model_id="model_flash",
            compiler_version="compiler_v2",
            batch_size=2,
            max_batch_chars=10000,
        )

        extraction = SemanticBatchExtraction(
            entities=[
                SemanticEntity(tempId="org_1", className="organization"),
                SemanticEntity(tempId="person_1", className="person"),
            ],
            chunks=[
                {
                    "chunkIndex": 0,
                    "claims": [
                        {
                            "claimId": "c1",
                            "statement": "CLB Taekwondo Vạn Quán",
                            "evidence": {"chunkIndex": 0, "text": "CLB Taekwondo Vạn Quán"},
                            "outcome": "MAPPED",
                            "mapping": {
                                "kind": "PROPERTY",
                                "entityRef": "org_1",
                                "propertyName": "name",
                                "value": "CLB Taekwondo Vạn Quán",
                            },
                        },
                        {
                            "claimId": "c2",
                            "statement": "Người sáng lập là Phùng Thế Lịch",
                            "evidence": {"chunkIndex": 0, "text": "Phùng Thế Lịch"},
                            "outcome": "MAPPED",
                            "mapping": {
                                "kind": "PROPERTY",
                                "entityRef": "person_1",
                                "propertyName": "name",
                                "value": "Phùng Thế Lịch",
                            },
                        },
                        {
                            "claimId": "c3",
                            "statement": "Tổ chức được sáng lập bởi Phùng Thế Lịch",
                            "evidence": {"chunkIndex": 0, "text": "bởi Phùng Thế Lịch"},
                            "outcome": "MAPPED",
                            "mapping": {
                                "kind": "EDGE",
                                "edgeName": "founded_by",
                                "sourceRef": "org_1",
                                "targetRef": "person_1",
                            },
                        },
                    ],
                },
                {
                    "chunkIndex": 1,
                    "claims": [],
                    "noRelevantFactReason": "Lời ngỏ không chứa thông tin thực thể.",
                },
            ],
        )

        cache = MockOntologyCache(projection)
        result = await submit_batch(
            repository=repo,
            ontology_cache=cache,
            ingestion_id=str(workspace.job.id),
            batch_index=0,
            scope_keys=["organization"],
            semantic_fragment=extraction,
        )
        return result

    result = asyncio.run(scenario())
    assert result["success"] is True
    assert result["stage"] == "batch_staged"
    assert result["semanticSubmissions"] == 1


def test_schema_gap_transitions_to_schema_review_required():
    """When a claim represents a schema gap, batch transitions to schema_review_required, not repair."""
    async def scenario():
        repo = IngestionRepository()
        projection = _sample_projection()
        chunks = _create_sample_chunks()

        workspace, _, _ = await repo.create_or_resume(
            artifact_name="clb.md",
            content_hash="hash_1",
            chunks=chunks,
            ontology=type("Ont", (), {"version_id": projection.version_id, "version": projection.version})(),
            document_key="clb",
            scope_hint=None,
            skill_digest="skill_v1",
            model_id="model_flash",
            compiler_version="compiler_v2",
            batch_size=2,
            max_batch_chars=10000,
        )

        extraction = SemanticBatchExtraction(
            entities=[
                SemanticEntity(tempId="org_1", className="organization"),
            ],
            chunks=[
                {
                    "chunkIndex": 0,
                    "claims": [
                        {
                            "claimId": "c1",
                            "statement": "CLB Taekwondo Vạn Quán",
                            "evidence": {"chunkIndex": 0, "text": "CLB Taekwondo Vạn Quán"},
                            "outcome": "MAPPED",
                            "mapping": {
                                "kind": "PROPERTY",
                                "entityRef": "org_1",
                                "propertyName": "name",
                                "value": "CLB Taekwondo Vạn Quán",
                            },
                        },
                        {
                            "claimId": "c_gap",
                            "statement": "Có 6 cơ sở hoạt động",
                            "evidence": {"chunkIndex": 0, "text": "có 6 cơ sở"},
                            "outcome": "SCHEMA_GAP",
                            "schemaGap": {
                                "kind": "PROPERTY",
                                "entityRef": "org_1",
                                "technicalName": "facility_count",
                                "displayName": "Số lượng cơ sở",
                                "dataType": "INTEGER",
                                "value": 6,
                                "reason": "Cần lưu số cơ sở của CLB",
                            },
                        },
                    ],
                },
                {
                    "chunkIndex": 1,
                    "claims": [],
                    "noRelevantFactReason": "Lời chào mở đầu",
                },
            ],
        )

        cache = MockOntologyCache(projection)
        result = await submit_batch(
            repository=repo,
            ontology_cache=cache,
            ingestion_id=str(workspace.job.id),
            batch_index=0,
            scope_keys=["organization"],
            semantic_fragment=extraction,
        )
        return result, repo, str(workspace.job.id)

    result, repo, ingestion_id = asyncio.run(scenario())
    assert result["success"] is True
    assert result["stage"] == "schema_review_required"
    assert result["retryRequired"] is False
    assert result["nextAction"] == "review_schema_proposal"
    assert len(result["schemaGaps"]) == 1


def test_zero_llm_recompile_on_approved_proposal():
    """After user approves schema proposal, backend recompiles stored claim ledger without calling LLM."""
    async def scenario():
        repo = IngestionRepository()
        projection = _sample_projection()
        chunks = _create_sample_chunks()

        workspace, _, _ = await repo.create_or_resume(
            artifact_name="clb.md",
            content_hash="hash_1",
            chunks=chunks,
            ontology=type("Ont", (), {"version_id": projection.version_id, "version": projection.version})(),
            document_key="clb",
            scope_hint=None,
            skill_digest="skill_v1",
            model_id="model_flash",
            compiler_version="compiler_v2",
            batch_size=2,
            max_batch_chars=10000,
        )

        extraction = SemanticBatchExtraction(
            entities=[
                SemanticEntity(tempId="org_1", className="organization"),
            ],
            chunks=[
                {
                    "chunkIndex": 0,
                    "claims": [
                        {
                            "claimId": "c1",
                            "statement": "CLB Taekwondo Vạn Quán",
                            "evidence": {"chunkIndex": 0, "text": "CLB Taekwondo Vạn Quán"},
                            "outcome": "MAPPED",
                            "mapping": {
                                "kind": "PROPERTY",
                                "entityRef": "org_1",
                                "propertyName": "name",
                                "value": "CLB Taekwondo Vạn Quán",
                            },
                        },
                        {
                            "claimId": "c_gap",
                            "statement": "Có 6 cơ sở hoạt động",
                            "evidence": {"chunkIndex": 0, "text": "có 6 cơ sở"},
                            "outcome": "SCHEMA_GAP",
                            "schemaGap": {
                                "kind": "PROPERTY",
                                "entityRef": "org_1",
                                "technicalName": "facility_count",
                                "displayName": "Số lượng cơ sở",
                                "dataType": "INTEGER",
                                "value": 6,
                                "reason": "Cần lưu số cơ sở của CLB",
                            },
                        },
                    ],
                },
                {
                    "chunkIndex": 1,
                    "claims": [],
                    "noRelevantFactReason": "Lời chào mở đầu",
                },
            ],
        )

        cache = MockOntologyCache(projection)
        # 1. First submit -> blocks for schema
        initial_result = await submit_batch(
            repository=repo,
            ontology_cache=cache,
            ingestion_id=str(workspace.job.id),
            batch_index=0,
            scope_keys=["organization"],
            semantic_fragment=extraction,
        )
        assert initial_result["stage"] == "schema_review_required"

        # 2. Simulate approval and updated ontology version with new property
        updated_projection_dict = projection.model_dump(by_alias=True, mode="json")
        updated_projection_dict["properties"].append({
            "id": "p_org_facility_count",
            "entityType": "organization",
            "technicalName": "facility_count",
            "dataType": "INTEGER",
        })
        updated_projection = OntologyProjection.model_validate(updated_projection_dict)
        updated_cache = MockOntologyCache(updated_projection)

        # 3. Backend recompiles from stored ledger without calling LLM!
        recompile_result = await recompile_batch_from_ledger(
            repository=repo,
            ontology_cache=updated_cache,
            ingestion_id=str(workspace.job.id),
            batch_index=0,
            approved_proposals=[{"technicalName": "facility_count"}],
        )
        return recompile_result

    recompile_result = asyncio.run(scenario())
    assert recompile_result["success"] is True
    assert recompile_result["stage"] == "batch_staged"


def test_hard_validation_errors_reject_extraction():
    """Un-grounded evidence or missing identity property immediately triggers EXTRACTION_REJECTED."""
    async def scenario():
        repo = IngestionRepository()
        projection = _sample_projection()
        chunks = _create_sample_chunks()

        workspace, _, _ = await repo.create_or_resume(
            artifact_name="clb.md",
            content_hash="hash_1",
            chunks=chunks,
            ontology=type("Ont", (), {"version_id": projection.version_id, "version": projection.version})(),
            document_key="clb",
            scope_hint=None,
            skill_digest="skill_v1",
            model_id="model_flash",
            compiler_version="compiler_v2",
            batch_size=2,
            max_batch_chars=10000,
        )

        # Evidence text is NOT in chunk 0!
        extraction = SemanticBatchExtraction(
            entities=[
                SemanticEntity(tempId="org_1", className="organization"),
            ],
            chunks=[
                {
                    "chunkIndex": 0,
                    "claims": [
                        {
                            "claimId": "c1",
                            "statement": "Hoàn toàn bịa đặt",
                            "evidence": {"chunkIndex": 0, "text": "Chuỗi này hoàn toàn không có trong văn bản gốc"},
                            "outcome": "MAPPED",
                            "mapping": {
                                "kind": "PROPERTY",
                                "entityRef": "org_1",
                                "propertyName": "name",
                                "value": "CLB Bịa Đặt",
                            },
                        }
                    ],
                },
                {
                    "chunkIndex": 1,
                    "claims": [],
                    "noRelevantFactReason": "Lời chào mở đầu",
                },
            ],
        )

        cache = MockOntologyCache(projection)
        result = await submit_batch(
            repository=repo,
            ontology_cache=cache,
            ingestion_id=str(workspace.job.id),
            batch_index=0,
            scope_keys=["organization"],
            semantic_fragment=extraction,
        )
        return result

    result = asyncio.run(scenario())
    assert result["success"] is False
    assert result["stage"] == "extraction_rejected"
    assert result["retryRequired"] is False
    assert result["nextAction"] == "report_extraction_failure"
    assert any(e["code"] == "EVIDENCE_NOT_GROUNDED" for e in result["errors"])


def test_omitted_outcome_is_auto_inferred():
    """When LLM omits outcome in a claim JSON, schema auto-infers from mapping or schemaGap."""
    raw_payload = {
        "entities": [{"tempId": "org_1", "className": "organization"}],
        "chunks": [
            {
                "chunkIndex": 0,
                "claims": [
                    {
                        "claimId": "c1",
                        "statement": "Tên tổ chức",
                        "evidence": {"chunkIndex": 0, "text": "CLB Taekwondo Vạn Quán"},
                        # outcome is omitted!
                        "mapping": {
                            "kind": "PROPERTY",
                            "entityRef": "org_1",
                            "propertyName": "name",
                            "value": "CLB Taekwondo Vạn Quán",
                        },
                    },
                    {
                        "claimId": "c2",
                        "statement": "Có 6 cơ sở",
                        "evidence": {"chunkIndex": 0, "text": "có 6 cơ sở"},
                        # outcome is omitted!
                        "schemaGap": {
                            "kind": "PROPERTY",
                            "entityRef": "org_1",
                            "technicalName": "facility_count",
                            "dataType": "INTEGER",
                            "value": 6,
                        },
                    },
                ],
            }
        ],
    }

    extraction = SemanticBatchExtraction.model_validate(raw_payload)
    assert extraction.chunks[0].claims[0].outcome == "MAPPED"
    assert extraction.chunks[0].claims[1].outcome == "SCHEMA_GAP"


def test_markdown_syntax_differences_in_evidence_are_grounded():
    """Verify that quote variations omitting asterisks ** or blockquotes > still ground correctly."""
    compiler = BatchClaimCompiler()
    projection = _sample_projection()
    source_chunks = [
        PreparedChunk(
            chunk_id="c5",
            chunk_index=5,
            text="Sau 7 năm phát triển, năm **2019**, CLB Taekwondo Văn Quán chính thức sử dụng tên:\n\n> **HỆ THỐNG TAEKWONDO VĂN QUÁN**",
            content_hash="h5",
            token_count=30,
            source_anchor="anchor5",
        )
    ]

    extraction = SemanticBatchExtraction(
        entities=[{"tempId": "org_1", "className": "organization"}],
        chunks=[
            {
                "chunkIndex": 5,
                "claims": [
                    {
                        "claimId": "c_5_1",
                        "statement": "Tên tổ chức",
                        "evidence": {
                            "chunkIndex": 5,
                            # Notice: No asterisks around 2019, plain text
                            "text": "năm 2019, CLB Taekwondo Văn Quán chính thức sử dụng tên:\n\n> HỆ THỐNG TAEKWONDO VĂN QUÁN",
                        },
                        "outcome": "MAPPED",
                        "mapping": {
                            "kind": "PROPERTY",
                            "entityRef": "org_1",
                            "propertyName": "name",
                            "value": "HỆ THỐNG TAEKWONDO VĂN QUÁN",
                        },
                    }
                ],
            }
        ],
    )

    result = compiler.compile(
        extraction=extraction,
        ontology_projection=projection,
        source_chunks=source_chunks,
    )

    assert not result.errors
    assert result.fragment is not None
    assert len(result.fragment.coverage) == 1
    assert result.fragment.coverage[0].decision == "MAPPED"




def test_evidence_ref_is_authoritative_and_replaces_model_text():
    compiler = BatchClaimCompiler()
    projection = _sample_projection()
    source_chunks = [
        PreparedChunk(
            chunk_id="c0",
            chunk_index=0,
            text="Tên chính thức:\nHỆ THỐNG TAEKWONDO VĂN QUÁN",
            content_hash="h0",
            token_count=12,
            source_anchor="doc#0",
        )
    ]
    extraction = SemanticBatchExtraction.model_validate(
        {
            "entities": [{"tempId": "org_1", "className": "organization"}],
            "chunks": [
                {
                    "chunkIndex": 0,
                    "claims": [
                        {
                            "claimId": "c1",
                            "statement": "Tên tổ chức",
                            "evidence": {
                                "chunkIndex": 0,
                                "evidenceRef": "chunk:0:line:2",
                                "text": "MODEL TỰ GỬI TEXT SAI",
                            },
                            "outcome": "MAPPED",
                            "mapping": {
                                "kind": "PROPERTY",
                                "entityRef": "org_1",
                                "propertyName": "name",
                                "value": "HỆ THỐNG TAEKWONDO VĂN QUÁN",
                            },
                        }
                    ],
                }
            ],
        }
    )

    result = compiler.compile(
        extraction=extraction,
        ontology_projection=projection,
        source_chunks=source_chunks,
    )

    assert result.errors == []
    assert result.fragment is not None
    evidence = result.fragment.nodes[0].properties[0].evidence[0]
    assert evidence.evidence_ref == "chunk:0:line:2"
    assert evidence.text == "HỆ THỐNG TAEKWONDO VĂN QUÁN"
    assert result.canonical_claims[0]["evidence"]["text"] == "HỆ THỐNG TAEKWONDO VĂN QUÁN"


def test_property_value_must_be_supported_by_its_evidence():
    compiler = BatchClaimCompiler()
    projection = _sample_projection()
    source_chunks = [
        PreparedChunk(
            chunk_id="c0",
            chunk_index=0,
            text="Hệ thống được thành lập năm 2012. Đến năm 2019, CLB chính thức sử dụng tên mới.",
            content_hash="h0",
            token_count=20,
            source_anchor="doc#0",
        )
    ]
    extraction = SemanticBatchExtraction.model_validate(
        {
            "entities": [{"tempId": "org_1", "className": "organization"}],
            "chunks": [
                {
                    "chunkIndex": 0,
                    "claims": [
                        {
                            "claimId": "c0",
                            "statement": "Tên tổ chức",
                            "evidence": {
                                "chunkIndex": 0,
                                "evidenceRef": "chunk:0:line:1",
                            },
                            "outcome": "MAPPED",
                            "mapping": {
                                "kind": "PROPERTY",
                                "entityRef": "org_1",
                                "propertyName": "name",
                                "value": "Hệ thống",
                            },
                        },
                        {
                            "claimId": "c1",
                            "statement": "Hệ thống được thành lập năm 2019",
                            "evidence": {
                                "chunkIndex": 0,
                                "evidenceRef": "chunk:0:line:1",
                            },
                            "outcome": "MAPPED",
                            "mapping": {
                                "kind": "PROPERTY",
                                "entityRef": "org_1",
                                "propertyName": "founded_date",
                                "value": "2019",
                            },
                        }
                    ],
                }
            ],
        }
    )

    result = compiler.compile(
        extraction=extraction,
        ontology_projection=projection,
        source_chunks=source_chunks,
    )

    # The source span does contain "2019", so lexical value support alone is not enough
    # to prove the semantic role "founding date". This test therefore asserts the guard
    # only blocks values absent from evidence; semantic relation/role inference is out
    # of scope by design.
    assert result.errors == []
    assert result.fragment is not None

    extraction.chunks[0].claims[1].mapping.value = "2020"
    result = compiler.compile(
        extraction=extraction,
        ontology_projection=projection,
        source_chunks=source_chunks,
    )
    assert [error["code"] for error in result.errors] == [
        "PROPERTY_VALUE_NOT_SUPPORTED_BY_EVIDENCE"
    ]


def test_iso_date_value_is_supported_by_verbatim_vietnamese_date():
    compiler = BatchClaimCompiler()
    projection = _sample_projection()
    source_chunks = [
        PreparedChunk(
            chunk_id="c0",
            chunk_index=0,
            text="CLB thành lập ngày 15/05/2012.",
            content_hash="h0",
            token_count=8,
            source_anchor="doc#0",
        )
    ]
    extraction = SemanticBatchExtraction.model_validate(
        {
            "entities": [{"tempId": "org_1", "className": "organization"}],
            "chunks": [
                {
                    "chunkIndex": 0,
                    "claims": [
                        {
                            "claimId": "c0",
                            "statement": "Tên tổ chức",
                            "evidence": {
                                "chunkIndex": 0,
                                "evidenceRef": "chunk:0:line:1",
                            },
                            "outcome": "MAPPED",
                            "mapping": {
                                "kind": "PROPERTY",
                                "entityRef": "org_1",
                                "propertyName": "name",
                                "value": "CLB",
                            },
                        },
                        {
                            "claimId": "c1",
                            "statement": "Ngày thành lập",
                            "evidence": {
                                "chunkIndex": 0,
                                "evidenceRef": "chunk:0:line:1",
                            },
                            "outcome": "MAPPED",
                            "mapping": {
                                "kind": "PROPERTY",
                                "entityRef": "org_1",
                                "propertyName": "founded_date",
                                "value": "2012-05-15",
                            },
                        }
                    ],
                }
            ],
        }
    )

    result = compiler.compile(
        extraction=extraction,
        ontology_projection=projection,
        source_chunks=source_chunks,
    )
    assert result.errors == []
    assert result.fragment is not None


def test_proposal_lookup_fallback_by_ingestion_id():
    """Verify that review_proposal and apply_proposal can resolve a proposal by its source_ingestion_id."""
    async def scenario():
        container = await get_service_container()
        ingestion_id = "test-proposal-fallback-" + uuid.uuid4().hex[:8]
        active_version = await container.ontology_lifecycle._cache.active_version()

        proposal = await container.ontology_lifecycle.create_proposal(
            ingestion_id=ingestion_id,
            batch_index=1,
            ontology_version_id=str(active_version.version_id),
            source_document_id=None,
            proposal_type="NEW_PROPERTY",
            technical_name="anniversary_date",
            reason="Test anniversary date",
            payload={
                "displayName": "Ngày kỷ niệm",
                "entityType": "organization",
                "dataType": "STRING",
                "technicalName": "anniversary_date",
                "required": False,
            },
            evidence={"chunkIndex": 6, "quote": "Ngày kỷ niệm"},
            affected_scope_keys=["fundamentals"],
        )

        # 1. Review proposal using ingestion_id instead of proposal.id
        reviewed = await container.ontology_lifecycle.review_proposal(
            ingestion_id, approved=True, reviewed_by="tester"
        )
        assert reviewed.id == proposal.id
        assert reviewed.status.value == "APPROVED"

        # 2. Apply proposal using ingestion_id
        applied_version = await container.ontology_lifecycle.apply_proposal(
            ingestion_id, new_version_code=f"v_test_{uuid.uuid4().hex[:6]}", applied_by="tester"
        )
        assert applied_version is not None
        assert applied_version.status.value == "ACTIVE"

    asyncio.run(scenario())
