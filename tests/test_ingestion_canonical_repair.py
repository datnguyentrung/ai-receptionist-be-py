import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.schemas.ingestion_schema import (
    ChunkCoverage,
    DuplicateFactClaim,
    Evidence,
    GraphNode,
    GraphPatchFragment,
    OntologyProjection,
    PreparedChunk,
    PropertyFact,
    SemanticGraphPatchFragment,
)
from app.services.ingestion.fact_references import build_fact_index, property_fact_ref
from app.services.ingestion.graph_patch_compiler import GraphPatchCompiler
from app.services.ingestion.ontology import (
    OntologyRegistry,
    validate_coverage_integrity,
)
from app.services.ingestion.operations import finalize
from app.services.ingestion.repair_guard import RepairGuard


def _proj() -> OntologyProjection:
    return OntologyProjection.model_validate(
        {
            "versionId": "00000000-0000-0000-0000-000000000001",
            "version": "v1.1.0",
            "digest": "d1",
            "scopeKey": "taekwondo",
            "scopeKeys": ["taekwondo"],
            "entityTypes": [
                {
                    "technicalName": "person",
                    "displayName": "Person",
                    "identityStrategy": {"type": "NATURAL_KEY", "required": ["name"]},
                },
                {
                    "technicalName": "location",
                    "displayName": "Location",
                    "identityStrategy": {"type": "NATURAL_KEY", "required": ["name"]},
                },
                {
                    "technicalName": "schedule",
                    "displayName": "Schedule",
                    "identityStrategy": {"type": "NATURAL_KEY", "required": ["name"]},
                },
            ],
            "properties": [
                {"entityType": "person", "technicalName": "name", "dataType": "STRING", "required": True},
                {"entityType": "person", "technicalName": "phone", "dataType": "STRING", "required": False},
                {"entityType": "person", "technicalName": "role", "dataType": "STRING", "required": False},
                {"entityType": "location", "technicalName": "name", "dataType": "STRING", "required": True},
                {"entityType": "location", "technicalName": "address", "dataType": "STRING", "required": False},
                {"entityType": "schedule", "technicalName": "name", "dataType": "STRING", "required": True},
                {"entityType": "schedule", "technicalName": "start_time", "dataType": "STRING", "required": False},
            ],
            "relationships": [
                {
                    "technicalName": "has_schedule",
                    "sourceEntityType": "location",
                    "targetEntityType": "schedule",
                }
            ],
            "aliases": [],
        }
    )


# Case 1: Missing ontologyVersion in semantic payload passes validation effortlessly
def test_case_1_missing_ontology_version_passes() -> None:
    payload = {
        "nodes": [
            {
                "tempId": "p1",
                "className": "person",
                "properties": [
                    {
                        "propertyName": "name",
                        "value": "Phùng Thế Lịch",
                        "evidence": [{"source": "doc.md", "chunkIndex": 0, "text": "Phùng Thế Lịch"}],
                    }
                ],
            }
        ],
        "edges": [],
        "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "Founder"}],
    }
    sem = SemanticGraphPatchFragment.model_validate(payload)
    compiled = GraphPatchCompiler().compile(sem, _proj())
    assert compiled.fragment is not None
    assert compiled.fragment.ontology_version == "00000000-0000-0000-0000-000000000001"


# Case 2: tempId changes, same natural identity -> PASS
def test_case_2_tempid_changes_same_natural_identity_passes() -> None:
    proj = _proj()
    sem1 = SemanticGraphPatchFragment.model_validate(
        {
            "nodes": [
                {
                    "tempId": "loc_van_quan",
                    "className": "location",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": "Cơ sở 1 — Văn Quán",
                            "evidence": [{"source": "doc.md", "chunkIndex": 0, "text": "Cơ sở 1 — Văn Quán"}],
                        }
                    ],
                }
            ],
            "edges": [],
            "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "Location"}],
        }
    )
    sem2 = SemanticGraphPatchFragment.model_validate(
        {
            "nodes": [
                {
                    "tempId": "loc_vanquan",  # changed tempId!
                    "className": "location",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": "Cơ sở 1 — Văn Quán",
                            "evidence": [{"source": "doc.md", "chunkIndex": 0, "text": "Cơ sở 1 — Văn Quán"}],
                        }
                    ],
                }
            ],
            "edges": [],
            "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "Location"}],
        }
    )
    can1 = GraphPatchCompiler().compile(sem1, proj).fragment
    can2 = GraphPatchCompiler().compile(sem2, proj).fragment
    issues = RepairGuard.compare(previous_canonical_fragment=can1, new_canonical_fragment=can2)
    assert issues == []


# Case 3: tempId changes, actual identity changes -> detected as dropped valid node
def test_case_3_actual_identity_dropped_detected() -> None:
    proj = _proj()
    sem1 = SemanticGraphPatchFragment.model_validate(
        {
            "nodes": [
                {
                    "tempId": "loc_1",
                    "className": "location",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": "Cơ sở 1 — Văn Quán",
                            "evidence": [{"source": "doc.md", "chunkIndex": 0, "text": "Cơ sở 1 — Văn Quán"}],
                        }
                    ],
                }
            ],
            "edges": [],
            "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "loc1"}],
        }
    )
    sem2 = SemanticGraphPatchFragment.model_validate(
        {
            "nodes": [
                {
                    "tempId": "loc_2",
                    "className": "location",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": "Cơ sở 2 — Hà Đông",  # completely different entity
                            "evidence": [{"source": "doc.md", "chunkIndex": 0, "text": "Cơ sở 2 — Hà Đông"}],
                        }
                    ],
                }
            ],
            "edges": [],
            "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "loc2"}],
        }
    )
    can1 = GraphPatchCompiler().compile(sem1, proj).fragment
    can2 = GraphPatchCompiler().compile(sem2, proj).fragment
    issues = RepairGuard.compare(previous_canonical_fragment=can1, new_canonical_fragment=can2)
    assert any(i.code == "REPAIR_DROPPED_VALID_NODE" for i in issues)


# Case 4: Coverage repair adds valid property -> PASS
def test_case_4_coverage_repair_adds_valid_property_passes() -> None:
    proj = _proj()
    sem1 = SemanticGraphPatchFragment.model_validate(
        {
            "nodes": [
                {
                    "tempId": "p1",
                    "className": "person",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": "Phùng Thế Lịch",
                            "evidence": [{"source": "doc.md", "chunkIndex": 0, "text": "Phùng Thế Lịch"}],
                        }
                    ],
                }
            ],
            "edges": [],
            "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "name"}],
        }
    )
    sem2 = SemanticGraphPatchFragment.model_validate(
        {
            "nodes": [
                {
                    "tempId": "p1",
                    "className": "person",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": "Phùng Thế Lịch",
                            "evidence": [{"source": "doc.md", "chunkIndex": 0, "text": "Phùng Thế Lịch"}],
                        },
                        {
                            "propertyName": "phone",
                            "value": "033 999 8191",
                            "evidence": [{"source": "doc.md", "chunkIndex": 1, "text": "033 999 8191"}],
                        },
                    ],
                }
            ],
            "edges": [],
            "coverage": [
                {"chunkIndex": 0, "decision": "MAPPED", "reason": "name"},
                {"chunkIndex": 1, "decision": "MAPPED", "reason": "phone"},
            ],
        }
    )
    can1 = GraphPatchCompiler().compile(sem1, proj).fragment
    can2 = GraphPatchCompiler().compile(sem2, proj).fragment
    issues = RepairGuard.compare(
        previous_canonical_fragment=can1,
        new_canonical_fragment=can2,
        previous_validation_issues=[{"code": "MAPPED_WITHOUT_MAPPING", "location": "coverage.1.decision"}],
    )
    assert issues == []


# Case 5: Coverage repair removes old valid property -> REPAIR_DROPPED_VALID_FACT
def test_case_5_repair_removes_old_valid_property_fails() -> None:
    proj = _proj()
    sem1 = SemanticGraphPatchFragment.model_validate(
        {
            "nodes": [
                {
                    "tempId": "p1",
                    "className": "person",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": "Phùng Thế Lịch",
                            "evidence": [{"source": "doc.md", "chunkIndex": 0, "text": "Phùng Thế Lịch"}],
                        },
                        {
                            "propertyName": "phone",
                            "value": "033 999 8191",
                            "evidence": [{"source": "doc.md", "chunkIndex": 0, "text": "033 999 8191"}],
                        },
                    ],
                }
            ],
            "edges": [],
            "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "person"}],
        }
    )
    sem2 = SemanticGraphPatchFragment.model_validate(
        {
            "nodes": [
                {
                    "tempId": "p1",
                    "className": "person",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": "Phùng Thế Lịch",
                            "evidence": [{"source": "doc.md", "chunkIndex": 0, "text": "Phùng Thế Lịch"}],
                        }
                    ],
                }
            ],
            "edges": [],
            "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "person"}],
        }
    )
    can1 = GraphPatchCompiler().compile(sem1, proj).fragment
    can2 = GraphPatchCompiler().compile(sem2, proj).fragment
    issues = RepairGuard.compare(previous_canonical_fragment=can1, new_canonical_fragment=can2)
    assert any(i.code == "REPAIR_DROPPED_VALID_FACT" for i in issues)


# Case 6: Failed attempt contains garbage node -> garbage node NOT promoted to repair baseline
def test_case_6_garbage_node_not_promoted_to_baseline() -> None:
    proj = _proj()
    # Canonical fragment with 1 valid node and 1 invalid node that had issues
    can1 = GraphPatchFragment(
        ontology_version=proj.version_id,
        nodes=[
            GraphNode(
                temp_id="valid_person_key",
                class_name="person",
                identity={"name": "Phùng Thế Lịch"},
                properties=[
                    PropertyFact(
                        property_name="name",
                        value="Phùng Thế Lịch",
                        evidence=[Evidence(source="doc.md", chunk_index=0, text="Phùng Thế Lịch")],
                    )
                ],
            ),
            GraphNode(
                temp_id="garbage_node_key",
                class_name="person",
                identity={"name": "Garbage"},
                properties=[
                    PropertyFact(
                        property_name="name",
                        value="Garbage",
                        evidence=[Evidence(source="doc.md", chunk_index=0, text="Not in text")],
                    )
                ],
            ),
        ],
        edges=[],
        coverage=[],
    )
    issues_on_attempt1 = [
        {"code": "EVIDENCE_NOT_GROUNDED", "location": "nodes.1.properties.0.evidence.0.text"}
    ]
    baseline = RepairGuard.extract_validated_baseline(can1, issues_on_attempt1)
    assert baseline is not None
    assert len(baseline.nodes) == 1
    assert baseline.nodes[0].temp_id == "valid_person_key"

    # Attempt 2 removes garbage node and only keeps valid node
    can2 = GraphPatchFragment(
        ontology_version=proj.version_id,
        nodes=[baseline.nodes[0]],
        edges=[],
        coverage=[],
    )
    repair_issues = RepairGuard.compare(previous_canonical_fragment=baseline, new_canonical_fragment=can2)
    assert repair_issues == []


# Case 7: MAPPED + only node.evidence -> MAPPED_WITHOUT_MAPPING
def test_case_7_mapped_with_only_node_evidence_fails() -> None:
    chunks = [PreparedChunk(chunk_id="c0", chunk_index=0, text="Hệ thống Taekwondo Văn Quán", content_hash="h0", token_count=5, source_anchor="doc#0")]
    fragment = GraphPatchFragment(
        ontology_version="v1.1.0",
        nodes=[
            GraphNode(
                temp_id="loc_1",
                class_name="location",
                identity={"name": "Cơ sở 1"},
                properties=[],
                evidence=[Evidence(source="doc.md", chunk_index=0, text="Hệ thống Taekwondo Văn Quán")],
            )
        ],
        edges=[],
        coverage=[ChunkCoverage(chunk_index=0, decision="MAPPED", reason="Only mentioned")],
    )
    issues = validate_coverage_integrity(fragment, chunks)
    assert any(i.code == "MAPPED_WITHOUT_MAPPING" for i in issues)


# Case 8: MAPPED + property.evidence -> PASS
def test_case_8_mapped_with_property_evidence_passes() -> None:
    chunks = [PreparedChunk(chunk_id="c0", chunk_index=0, text="Hệ thống Taekwondo Văn Quán", content_hash="h0", token_count=5, source_anchor="doc#0")]
    fragment = GraphPatchFragment(
        ontology_version="v1.1.0",
        nodes=[
            GraphNode(
                temp_id="loc_1",
                class_name="location",
                identity={"name": "Cơ sở 1"},
                properties=[
                    PropertyFact(
                        property_name="name",
                        value="Cơ sở 1",
                        evidence=[Evidence(source="doc.md", chunk_index=0, text="Hệ thống Taekwondo Văn Quán")],
                    )
                ],
            )
        ],
        edges=[],
        coverage=[ChunkCoverage(chunk_index=0, decision="MAPPED", reason="Fact extracted")],
    )
    issues = validate_coverage_integrity(fragment, chunks)
    assert issues == []


# Case 9: DUPLICATE_EVIDENCE + resolvable prior fact -> PASS
def test_case_9_duplicate_evidence_with_resolvable_fact_passes() -> None:
    chunks = [
        PreparedChunk(chunk_id="c0", chunk_index=0, text="HLV Phùng Thế Lịch", content_hash="h0", token_count=5, source_anchor="doc#0"),
        PreparedChunk(chunk_id="c1", chunk_index=1, text="Phùng Thế Lịch là huấn luyện viên", content_hash="h1", token_count=5, source_anchor="doc#1"),
    ]
    fragment = GraphPatchFragment(
        ontology_version="v1.1.0",
        nodes=[
            GraphNode(
                temp_id="p1",
                class_name="person",
                identity={"name": "Phùng Thế Lịch"},
                properties=[
                    PropertyFact(
                        property_name="name",
                        value="Phùng Thế Lịch",
                            evidence=[Evidence(source="doc.md", chunk_index=0, text="HLV Phùng Thế Lịch")],
                    )
                ],
            )
        ],
        edges=[],
        coverage=[
            ChunkCoverage(chunk_index=0, decision="MAPPED", reason="Fact"),
            ChunkCoverage(
                chunk_index=1,
                decision="DUPLICATE_EVIDENCE",
                reason="Duplicates the established person name",
                duplicate_claims=[DuplicateFactClaim(
                    fact_ref=property_fact_ref("p1", "name", "Phùng Thế Lịch"),
                    evidence=Evidence(
                        source="doc.md",
                        chunk_index=1,
                        text="Phùng Thế Lịch",
                    ),
                )],
            ),
        ],
    )
    issues = validate_coverage_integrity(
        fragment, chunks, available_facts=build_fact_index([fragment])
    )
    assert issues == []


# Case 10: DUPLICATE_EVIDENCE + no matching prior fact -> validation failure
def test_case_10_duplicate_evidence_without_prior_fact_fails() -> None:
    chunks = [PreparedChunk(chunk_id="c0", chunk_index=0, text="Text", content_hash="h0", token_count=1, source_anchor="doc#0")]
    fragment = GraphPatchFragment(
        ontology_version="v1.1.0",
        nodes=[],
        edges=[],
        coverage=[ChunkCoverage(chunk_index=0, decision="DUPLICATE_EVIDENCE", reason="No prior facts exist")],
    )
    issues = validate_coverage_integrity(fragment, chunks, external_node_types={})
    assert any(i.code == "DUPLICATE_WITHOUT_MATCHING_FACT" for i in issues)


# Case 11: UNSUPPORTED_BY_ONTOLOGY -> schema_gap_candidate
def test_case_11_unsupported_by_ontology_emits_schema_gap_candidate() -> None:
    chunks = [PreparedChunk(chunk_id="c0", chunk_index=0, text="Text", content_hash="h0", token_count=1, source_anchor="doc#0")]
    fragment = GraphPatchFragment(
        ontology_version="v1.1.0",
        nodes=[],
        edges=[],
        coverage=[ChunkCoverage(chunk_index=0, decision="UNSUPPORTED_BY_ONTOLOGY", reason="Needs sponsor entity")],
    )
    issues = validate_coverage_integrity(fragment, chunks)
    assert any(i.code == "SCHEMA_GAP_CANDIDATE" for i in issues)


# Case 12: AMBIGUOUS -> cannot finalize
def test_case_12_ambiguous_coverage_flagged() -> None:
    chunks = [PreparedChunk(chunk_id="c0", chunk_index=0, text="Text", content_hash="h0", token_count=1, source_anchor="doc#0")]
    fragment = GraphPatchFragment(
        ontology_version="v1.1.0",
        nodes=[],
        edges=[],
        coverage=[ChunkCoverage(chunk_index=0, decision="AMBIGUOUS", reason="Unclear subject")],
    )
    issues = validate_coverage_integrity(fragment, chunks)
    assert any(i.code == "COVERAGE_AMBIGUOUS" for i in issues)


# Case 13: FAILED -> cannot finalize
def test_case_13_failed_coverage_flagged() -> None:
    chunks = [PreparedChunk(chunk_id="c0", chunk_index=0, text="Text", content_hash="h0", token_count=1, source_anchor="doc#0")]
    fragment = GraphPatchFragment(
        ontology_version="v1.1.0",
        nodes=[],
        edges=[],
        coverage=[ChunkCoverage(chunk_index=0, decision="FAILED", reason="Parse error")],
    )
    issues = validate_coverage_integrity(fragment, chunks)
    assert any(i.code == "COVERAGE_FAILED" for i in issues)


# Case 14: Legacy bad STAGED batch -> finalize catches it
def test_case_14_finalize_catches_bad_staged_batch() -> None:
    # Set up workspace with a STAGED batch that has unevidenced MAPPED chunk
    bad_fragment = {
        "ontologyVersion": "v1.1.0",
        "nodes": [],
        "edges": [],
        "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "No fact"}],
    }
    batch = SimpleNamespace(
        batch_index=0,
        chunk_indexes=[0],
        status="STAGED",
        graph_fragment=bad_fragment,
        validation_issues=[],
        validation_attempts=1,
    )
    workspace = SimpleNamespace(
        job=SimpleNamespace(id=uuid4(), ontology_version_id=uuid4(), status="BATCHING"),
        document=SimpleNamespace(id=uuid4()),
        version=SimpleNamespace(id=uuid4(), ontology_version_id=uuid4()),
        chunks=(PreparedChunk(chunkId="c0", chunkIndex=0, text="Text", contentHash="h0", tokenCount=1, sourceAnchor="doc#0"),),
        batches=(batch,),
    )

    class Repo:
        async def get_workspace(self, _):
            return workspace
        async def mark_batch_for_repair(self, *_args, **_kwargs):
            return workspace

    class Cache:
        async def get_many(self, *_args):
            return _proj()

    workspace.document.name = "test.md"
    workspace.version.content_hash = "h"
    workspace.version.ontology_digest = "d1"
    batch.scope_keys = ["taekwondo"]
    batch.validated_baseline = None
    result = asyncio.run(finalize(Repo(), Cache(), "ingestion-id"))
    assert result["success"] is False
    assert result["stage"] == "repair_required"
    assert 0 in result["repairBatchIndexes"]


# Case 15: 23-chunk Taekwondo document flow simulation: batch 0 + batch 1 without cascading repair failure
def test_case_15_taekwondo_document_batch_flow() -> None:
    proj = _proj()
    # Batch 0: Chunks 0..4
    b0_payload = {
        "nodes": [
            {
                "tempId": "org_vq",
                "className": "location",
                "properties": [
                    {
                        "propertyName": "name",
                        "value": "Hệ Thống Taekwondo Văn Quán",
                        "evidence": [{"source": "doc.md", "chunkIndex": 2, "text": "Hệ Thống Taekwondo Văn Quán"}],
                    }
                ],
            }
        ],
        "edges": [],
        "coverage": [
            {"chunkIndex": 0, "decision": "NO_RELEVANT_FACT", "reason": "Tiêu đề tài liệu"},
            {"chunkIndex": 1, "decision": "NO_RELEVANT_FACT", "reason": "Giới thiệu chung"},
            {"chunkIndex": 2, "decision": "MAPPED", "reason": "Thông tin tổng quan"},
            {"chunkIndex": 3, "decision": "NO_RELEVANT_FACT", "reason": "Lịch sử thành lập 2012"},
            {"chunkIndex": 4, "decision": "NO_RELEVANT_FACT", "reason": "Phát triển 2019"},
        ],
    }
    sem0 = SemanticGraphPatchFragment.model_validate(b0_payload)
    can0 = GraphPatchCompiler().compile(sem0, proj).fragment
    chunks0 = [
        PreparedChunk(chunk_id=f"c{i}", chunk_index=i, text=f"Chunk {i} text", content_hash=f"h{i}", token_count=5, source_anchor=f"doc#{i}")
        for i in range(5)
    ]
    issues0 = validate_coverage_integrity(can0, chunks0)
    assert any(issue.code == "COVERAGE_REVIEW_REQUIRED" for issue in issues0)

    # Batch 1: Chunks 5..9 with location and schedule
    b1_payload = {
        "nodes": [
            {
                "tempId": "loc_vanquan",  # arbitrary tempId chosen by model
                "className": "location",
                "properties": [
                    {
                        "propertyName": "name",
                        "value": "Cơ sở 1 — Văn Quán",
                        "evidence": [{"source": "doc.md", "chunkIndex": 9, "text": "Cơ sở 1 — Văn Quán"}],
                    },
                    {
                        "propertyName": "address",
                        "value": "Sân sau Tòa nhà CT2A",
                        "evidence": [{"source": "doc.md", "chunkIndex": 9, "text": "Sân sau Tòa nhà CT2A"}],
                    },
                ],
            },
            {
                "tempId": "sched_cs1",
                "className": "schedule",
                "properties": [
                    {
                        "propertyName": "name",
                        "value": "Lịch tập Cơ sở 1",
                        "evidence": [{"source": "doc.md", "chunkIndex": 9, "text": "17:30 – 19:00"}],
                    }
                ],
            },
        ],
        "edges": [
            {
                "edgeName": "has_schedule",
                "sourceTempId": "loc_vanquan",
                "targetTempId": "sched_cs1",
                "properties": [],
                "evidence": [{"source": "doc.md", "chunkIndex": 9, "text": "17:30 – 19:00"}],
            }
        ],
        "coverage": [
            {"chunkIndex": 5, "decision": "DUPLICATE_EVIDENCE", "reason": "Đổi tên 2019 đã có ở batch 0"},
            {"chunkIndex": 6, "decision": "NO_RELEVANT_FACT", "reason": "Kỷ niệm 10 năm"},
            {"chunkIndex": 7, "decision": "NO_RELEVANT_FACT", "reason": "Tiểu sử thầy Lịch"},
            {"chunkIndex": 8, "decision": "NO_RELEVANT_FACT", "reason": "Tổng quan 6 cơ sở"},
            {"chunkIndex": 9, "decision": "MAPPED", "reason": "Cơ sở 1 và lịch tập"},
        ],
    }
    sem1 = SemanticGraphPatchFragment.model_validate(b1_payload)
    can1 = GraphPatchCompiler().compile(sem1, proj).fragment
    chunks1 = [
        PreparedChunk(chunk_id=f"c{i}", chunk_index=i, text=f"Chunk {i} text", content_hash=f"h{i}", token_count=5, source_anchor=f"doc#{i}")
        for i in range(5, 10)
    ]
    issues1 = validate_coverage_integrity(can1, chunks1)
    assert any(issue.code == "COVERAGE_REVIEW_REQUIRED" for issue in issues1)


# Case 16: Auto-evidence resolves from section and text even when LLM provides empty text quote
def test_case_16_auto_evidence_resolves_without_quote() -> None:
    chunk = PreparedChunk(
        chunk_id="c9",
        chunk_index=9,
        text="Địa điểm:\nSân sau Tòa nhà CT2A, Khu đô thị mới Văn Quán, phường Phúc La, quận Hà Đông, Hà Nội.\n\nLịch được công bố năm 2022:\n17:30 – 19:00, Thứ Tư và Thứ Bảy.",
        section="Cơ sở 1 — Văn Quán",
        content_hash="h9",
        token_count=50,
        source_anchor="doc#9",
    )
    fragment = GraphPatchFragment(
        ontology_version="v1.1.0",
        nodes=[
            GraphNode(
                temp_id="loc_1",
                class_name="location",
                identity={"name": "Cơ sở 1 — Văn Quán"},
                properties=[
                    PropertyFact(
                        property_name="name",
                        value="Cơ sở 1 — Văn Quán",
                        evidence=[Evidence(chunk_index=9)],  # empty text, python auto-resolves!
                    ),
                    PropertyFact(
                        property_name="address",
                        value="Sân sau Tòa nhà CT2A, Khu đô thị mới Văn Quán",
                        evidence=[Evidence(chunk_index=9)],  # empty text, python auto-resolves!
                    ),
                ],
            )
        ],
        edges=[],
        coverage=[ChunkCoverage(chunk_index=9, decision="MAPPED", reason="Resolved")],
    )
    from app.services.ingestion.evidence_guard import GraphFragmentEvidenceGuard
    guarded = GraphFragmentEvidenceGuard().canonicalize(fragment, [chunk])
    assert guarded.nodes[0].properties[0].evidence[0].text != ""
    assert "Cơ sở 1 — Văn Quán" in guarded.nodes[0].properties[0].evidence[0].text
    assert "Sân sau Tòa nhà CT2A" in guarded.nodes[0].properties[1].evidence[0].text

    issues = validate_coverage_integrity(guarded, [chunk])
    assert issues == []


# Case 17: Auto-evidence handles quote spanning section header and body (exact log case)
def test_case_17_auto_evidence_handles_quote_spanning_section_and_body() -> None:
    chunk = PreparedChunk(
        chunk_id="c9",
        chunk_index=9,
        text="Địa điểm:\nSân sau Tòa nhà CT2A, Khu đô thị mới Văn Quán, phường Phúc La, quận Hà Đông, Hà Nội.\n\nLịch được công bố năm 2022:\n17:30 – 19:00, Thứ Tư và Thứ Bảy.",
        section="Cơ sở 1 — Văn Quán",
        content_hash="h9",
        token_count=50,
        source_anchor="doc#9",
    )
    multiline_quote = "Cơ sở 1 — Văn Quán\n\nĐịa điểm:\nSân sau Tòa nhà CT2A, Khu đô thị mới Văn Quán, phường Phúc La, quận Hà Đông, Hà Nội."
    fragment = GraphPatchFragment(
        ontology_version="v1.1.0",
        nodes=[
            GraphNode(
                temp_id="loc_1",
                class_name="location",
                identity={"name": "Cơ sở 1 — Văn Quán"},
                properties=[
                    PropertyFact(
                        property_name="address",
                        value="Sân sau Tòa nhà CT2A",
                        evidence=[Evidence(chunk_index=9, text=multiline_quote)],
                    )
                ],
            )
        ],
        edges=[],
        coverage=[ChunkCoverage(chunk_index=9, decision="MAPPED", reason="Resolved")],
    )
    from app.services.ingestion.evidence_guard import GraphFragmentEvidenceGuard
    guarded = GraphFragmentEvidenceGuard().canonicalize(fragment, [chunk])
    issues = validate_coverage_integrity(guarded, [chunk])
    assert issues == []


# Case 18: Hallucinated evidence that truly doesn't exist still fails grounding
def test_case_18_hallucinated_evidence_still_fails_grounding() -> None:
    chunk = PreparedChunk(
        chunk_id="c9",
        chunk_index=9,
        text="Địa điểm:\nSân sau Tòa nhà CT2A.",
        section="Cơ sở 1 — Văn Quán",
        content_hash="h9",
        token_count=10,
        source_anchor="doc#9",
    )
    fragment = GraphPatchFragment(
        ontology_version="v1.1.0",
        nodes=[
            GraphNode(
                temp_id="loc_1",
                class_name="location",
                identity={"name": "Fake Name Never Found"},
                properties=[
                    PropertyFact(
                        property_name="name",
                        value="Fake Name Never Found",
                        evidence=[Evidence(chunk_index=9, text="Fake text never written anywhere")],
                    )
                ],
            )
        ],
        edges=[],
        coverage=[ChunkCoverage(chunk_index=9, decision="MAPPED", reason="Fake")],
    )
    from app.services.ingestion.evidence_guard import GraphFragmentEvidenceGuard
    guarded = GraphFragmentEvidenceGuard().canonicalize(fragment, [chunk])
    issues = OntologyRegistry(_proj()).validate_fragment(guarded, [chunk])
    # Must preserve defense against true hallucination!
    assert any(i.code == "EVIDENCE_NOT_GROUNDED" for i in issues)


# Case 19: LLM provides node with NO tempId and connects edge by natural name
def test_case_19_node_without_tempid_and_edge_by_natural_name() -> None:
    proj = _proj()
    payload = {
        "nodes": [
            {
                # No tempId supplied at all!
                "className": "location",
                "properties": [
                    {
                        "propertyName": "name",
                        "value": "Cơ sở 1 — Văn Quán",
                        "evidence": [{"chunkIndex": 0, "text": "Cơ sở 1 — Văn Quán"}],
                    }
                ],
            },
            {
                # No tempId supplied at all!
                "className": "schedule",
                "properties": [
                    {
                        "propertyName": "name",
                        "value": "Lịch tập Cơ sở 1",
                        "evidence": [{"chunkIndex": 0, "text": "Lịch tập Cơ sở 1"}],
                    }
                ],
            },
        ],
        "edges": [
            {
                "edgeName": "has_schedule",
                "sourceTempId": "Cơ sở 1 — Văn Quán",  # Natural name reference!
                "targetTempId": "Lịch tập Cơ sở 1",   # Natural name reference!
                "properties": [],
                "evidence": [{"chunkIndex": 0, "text": "17:30 – 19:00"}],
            }
        ],
        "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "Full batch"}],
    }
    sem = SemanticGraphPatchFragment.model_validate(payload)
    compile_res = GraphPatchCompiler().compile(sem, proj)
    assert compile_res.issues == []
    assert compile_res.fragment is not None
    assert len(compile_res.fragment.nodes) == 2
    assert len(compile_res.fragment.edges) == 1
    assert compile_res.fragment.edges[0].source_temp_id == compile_res.fragment.nodes[0].temp_id
    assert compile_res.fragment.edges[0].target_temp_id == compile_res.fragment.nodes[1].temp_id
