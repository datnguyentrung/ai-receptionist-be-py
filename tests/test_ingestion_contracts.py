from app.schemas.ingestion_schema import (
    GraphPatchFragment,
    OntologyProjection,
    PreparedChunk,
)
from app.services.ingestion.ontology import OntologyRegistry
from app.services.ingestion.document.strategies import canonicalize_markdown_plain_text
from app.services.ingestion.evidence_guard import GraphFragmentEvidenceGuard


def projection() -> OntologyProjection:
    return OntologyProjection.model_validate(
        {
            "versionId": "v-id",
            "version": "v1",
            "digest": "digest",
            "scopeKey": "course",
            "entityTypes": [{"technicalName": "course"}],
            "properties": [
                {
                    "entityType": "course",
                    "technicalName": "name",
                    "dataType": "STRING",
                }
            ],
            "relationships": [],
            "aliases": [],
        }
    )


def test_camel_case_graph_patch_and_grounded_evidence_validate() -> None:
    fragment = GraphPatchFragment.model_validate(
        {
            "ontologyVersion": "v1",
            "nodes": [
                {
                    "tempId": "c1",
                    "className": "course",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": "Taekwondo căn bản",
                            "evidence": [
                                {
                                    "source": "course.md",
                                    "chunkIndex": 0,
                                    "text": "Taekwondo căn bản",
                                }
                            ],
                        }
                    ],
                }
            ],
            "edges": [],
            "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "course"}],
        }
    )
    chunk = PreparedChunk(
        chunkId="chunk",
        chunkIndex=0,
        text="Khóa Taekwondo căn bản dành cho người mới",
        contentHash="hash",
        tokenCount=10,
        sourceAnchor="course.md#block-0",
    )

    assert OntologyRegistry(projection()).validate_fragment(fragment, [chunk]) == []
    assert fragment.model_dump(by_alias=True)["nodes"][0]["tempId"] == "c1"


def test_validator_rejects_ungrounded_evidence_and_wrong_datatype() -> None:
    fragment = GraphPatchFragment.model_validate(
        {
            "ontologyVersion": "v1",
            "nodes": [
                {
                    "tempId": "c1",
                    "className": "course",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": 42,
                            "evidence": [
                                {"source": "x", "chunkIndex": 0, "text": "không tồn tại"}
                            ],
                        }
                    ],
                }
            ],
            "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "course"}],
        }
    )
    chunk = PreparedChunk(
        chunkId="chunk",
        chunkIndex=0,
        text="nội dung khác",
        contentHash="hash",
        tokenCount=4,
        sourceAnchor="x#0",
    )

    codes = {item.code for item in OntologyRegistry(projection()).validate_fragment(fragment, [chunk])}
    assert {"PROPERTY_DATATYPE_MISMATCH", "EVIDENCE_NOT_GROUNDED"} <= codes


def test_markdown_table_and_bold_are_canonicalized_for_grounding() -> None:
    text = canonicalize_markdown_plain_text(
        "| **Tên chính thức** | **Hệ Thống Taekwondo Văn Quán** |\n"
        "| --- | --- |\n"
        "Địa chỉ: **CT2A**"
    )

    assert "Tên chính thức: Hệ Thống Taekwondo Văn Quán" in text
    assert "Địa chỉ: CT2A" in text
    assert "**" not in text


def test_ungrounded_evidence_includes_failure_reason() -> None:
    fragment = GraphPatchFragment.model_validate(
        {
            "ontologyVersion": "v1",
            "nodes": [
                {
                    "tempId": "c1",
                    "className": "course",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": "Taekwondo căn bản",
                            "evidence": [
                                {
                                    "source": "x",
                                    "chunkIndex": 0,
                                    "text": "Taekwondo cơ bản",
                                }
                            ],
                        }
                    ],
                }
            ],
            "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "course"}],
        }
    )
    chunk = PreparedChunk(
        chunkId="chunk",
        chunkIndex=0,
        text="Taekwondo căn bản",
        contentHash="hash",
        tokenCount=4,
        sourceAnchor="x#0",
    )

    issues = OntologyRegistry(projection()).validate_fragment(fragment, [chunk])

    assert [item.code for item in issues] == ["EVIDENCE_NOT_GROUNDED"]
    assert "TEXT_NOT_FOUND" in issues[0].message


def test_evidence_guard_completes_unique_truncated_prefix() -> None:
    fragment = GraphPatchFragment.model_validate(
        {
            "ontologyVersion": "v1",
            "nodes": [],
            "edges": [
                {
                    "edgeName": "located_at",
                    "sourceTempId": "org",
                    "targetTempId": "loc",
                    "evidence": [
                        {
                            "source": "x",
                            "chunkIndex": 8,
                            "text": (
                                "Hệ thống cơ sở tập luyện\n\n"
                                "Theo thông tin được Hệ Thống Taekwondo Văn Quán "
                                "công bố trong dịp kỷ niệm 10 năm năm 2022, h"
                            ),
                        }
                    ],
                }
            ],
            "coverage": [{"chunkIndex": 8, "decision": "MAPPED", "reason": "facility"}],
        }
    )
    chunk = PreparedChunk(
        chunkId="chunk",
        chunkIndex=8,
        text=(
            "Hệ thống cơ sở tập luyện\n\n"
            "Theo thông tin được Hệ Thống Taekwondo Văn Quán công bố trong dịp "
            "kỷ niệm 10 năm năm 2022, hệ thống có 06 cơ sở tập luyện."
        ),
        contentHash="hash",
        tokenCount=20,
        section="Hệ thống cơ sở tập luyện",
        sourceAnchor="doc.md#facility:L1-L3",
    )

    guarded = GraphFragmentEvidenceGuard().canonicalize(fragment, [chunk])

    evidence = guarded.edges[0].evidence[0]
    assert evidence.text in chunk.text
    assert evidence.text.endswith("06 cơ sở tập luyện.")
    assert evidence.source == "doc.md#facility:L1-L3"
    assert guarded.edges[0].source_temp_id == "org"
    assert guarded.edges[0].target_temp_id == "loc"


def test_evidence_guard_keeps_unmatched_quote_for_validator() -> None:
    fragment = GraphPatchFragment.model_validate(
        {
            "ontologyVersion": "v1",
            "nodes": [
                {
                    "tempId": "c1",
                    "className": "course",
                    "properties": [
                        {
                            "propertyName": "name",
                            "value": "Taekwondo căn bản",
                            "evidence": [
                                {
                                    "source": "x",
                                    "chunkIndex": 0,
                                    "text": "không tồn tại",
                                }
                            ],
                        }
                    ],
                }
            ],
            "edges": [],
            "coverage": [{"chunkIndex": 0, "decision": "MAPPED", "reason": "course"}],
        }
    )
    chunk = PreparedChunk(
        chunkId="chunk",
        chunkIndex=0,
        text="Taekwondo căn bản",
        contentHash="hash",
        tokenCount=4,
        sourceAnchor="x#0",
    )

    guarded = GraphFragmentEvidenceGuard().canonicalize(fragment, [chunk])
    issues = OntologyRegistry(projection()).validate_fragment(guarded, [chunk])

    assert guarded.nodes[0].properties[0].evidence[0].text == "không tồn tại"
    assert [item.code for item in issues] == ["EVIDENCE_NOT_GROUNDED"]
