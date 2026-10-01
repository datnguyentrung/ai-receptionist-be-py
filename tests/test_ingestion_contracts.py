from app.schemas.ingestion_schema import (
    GraphPatchFragment,
    OntologyProjection,
    PreparedChunk,
)
from app.services.ingestion.ontology import OntologyRegistry


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
