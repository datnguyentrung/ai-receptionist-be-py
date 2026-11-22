"""Regression tests for ontology-driven extraction contracts."""

import pytest

from app.schemas.ingestion.coverage import ChunkCoverage, PreparedChunk
from app.schemas.ingestion.grounding import Evidence, PropertyFact
from app.schemas.ingestion.ontology import OntologyProjection
from app.schemas.ingestion.semantic_patch import (
    SemanticGraphNode,
    SemanticGraphPatchFragment,
)
from app.services.ingestion.schema.extraction_contract import (
    build_entity_extraction_contract,
)
from app.services.ingestion.schema.graph_patch_compiled import GraphPatchCompiler
from app.services.ingestion.schema.ontology import OntologyRegistry
from app.utils.ingestion_helpers import stable_entity_key


def _projection(identity_fields: list[str]) -> OntologyProjection:
    return OntologyProjection(
        version_id="version-1",
        version="draft-1",
        digest="snapshot-1",
        scope_key="general",
        scope_keys=["general"],
        entity_types=[
            {
                "technicalName": "record",
                "identityStrategy": {"required": identity_fields},
            }
        ],
        properties=[
            {"entityType": "record", "technicalName": "code", "dataType": "STRING", "required": False},
            {"entityType": "record", "technicalName": "ordinal", "dataType": "INTEGER", "required": False},
        ],
        relationships=[],
        aliases=[],
    )


def _fact(name: str, value: object) -> PropertyFact:
    return PropertyFact(
        property_name=name,
        value=value,
        evidence=[Evidence(chunk_index=0, text=str(value))],
    )


def _coverage() -> list[ChunkCoverage]:
    return [ChunkCoverage(chunk_index=0, decision="MAPPED", reason="test")]


@pytest.mark.parametrize("identity_fields", [["code"], ["code", "ordinal"]])
def test_contract_and_compiler_follow_ontology_identity_fields(identity_fields) -> None:
    projection = _projection(identity_fields)
    contract = build_entity_extraction_contract(projection)
    entity = contract["entities"][0]
    assert entity["identityFields"] == identity_fields
    assert {item["technicalName"] for item in entity["properties"]} == {"code", "ordinal"}

    properties = [_fact("code", "A")]
    if "ordinal" in identity_fields:
        properties.append(_fact("ordinal", 7))
    fragment = SemanticGraphPatchFragment(
        nodes=[SemanticGraphNode(temp_id="one", class_name="record", properties=properties)],
        edges=[], coverage=_coverage(), warnings=[],
    )
    result = GraphPatchCompiler().compile(fragment, projection)
    assert not result.issues
    assert result.fragment.nodes[0].identity == {
        field: next(item.value for item in properties if item.property_name == field)
        for field in identity_fields
    }


def test_missing_identity_property_is_a_generic_compiler_error() -> None:
    projection = _projection(["code", "ordinal"])
    fragment = SemanticGraphPatchFragment(
        nodes=[
            SemanticGraphNode(
                temp_id="one", class_name="record",
                    properties=[_fact("code", "A")],
            )
        ],
        edges=[], coverage=_coverage(), warnings=[],
    )
    result = GraphPatchCompiler().compile(fragment, projection)
    assert [item.code for item in result.issues] == ["IDENTITY_SOURCE_PROPERTY_MISSING"]
    assert "ordinal" in result.issues[0].message


def test_canonical_reference_inherits_identity_and_accepts_new_property() -> None:
    projection = _projection(["code"])
    canonical_key = stable_entity_key("record", {"code": "A"})
    staged = {
        f"entity:{canonical_key}": {
            "ref": f"entity:{canonical_key}",
            "stableKey": canonical_key,
            "className": "record",
            "identity": {"code": "A"},
        }
    }
    fragment = SemanticGraphPatchFragment(
        nodes=[
            SemanticGraphNode(
                temp_id=canonical_key,
                class_name="record",
                properties=[_fact("ordinal", 7)],
            )
        ],
        edges=[],
        coverage=_coverage(),
        warnings=[],
    )

    result = GraphPatchCompiler().compile(
        fragment,
        projection,
        staged_entities=staged,
    )

    assert result.issues == []
    assert result.fragment is not None
    node = result.fragment.nodes[0]
    assert node.identity == {"code": "A"}
    assert [fact.property_name for fact in node.properties] == ["ordinal"]

    issues = OntologyRegistry(projection).validate_fragment(
        result.fragment,
        [
            PreparedChunk(
                chunk_id="chunk-1",
                chunk_index=0,
                text="7",
                content_hash="hash",
                token_count=1,
                source_anchor="doc.md#L1",
            )
        ],
        external_node_types={canonical_key: "record"},
    )
    assert issues == []


def test_canonical_reference_rejects_conflicting_identity_property() -> None:
    projection = _projection(["code"])
    canonical_key = stable_entity_key("record", {"code": "A"})
    staged = {
        f"entity:{canonical_key}": {
            "ref": f"entity:{canonical_key}",
            "stableKey": canonical_key,
            "className": "record",
            "identity": {"code": "A"},
        }
    }
    fragment = SemanticGraphPatchFragment(
        nodes=[
            SemanticGraphNode(
                temp_id=canonical_key,
                class_name="record",
                properties=[_fact("code", "B"), _fact("ordinal", 7)],
            )
        ],
        edges=[],
        coverage=_coverage(),
        warnings=[],
    )

    compiled = GraphPatchCompiler().compile(
        fragment,
        projection,
        staged_entities=staged,
    )
    assert compiled.fragment is not None
    issues = OntologyRegistry(projection).validate_fragment(
        compiled.fragment,
        [
            PreparedChunk(
                chunk_id="chunk-1",
                chunk_index=0,
                text="B 7",
                content_hash="hash",
                token_count=2,
                source_anchor="doc.md#L1",
            )
        ],
        external_node_types={canonical_key: "record"},
    )
    assert "IDENTITY_PROPERTY_MISMATCH" in {issue.code for issue in issues}


def test_canonical_reference_rejects_class_mismatch() -> None:
    projection = _projection(["code"])
    canonical_key = stable_entity_key("record", {"code": "A"})
    fragment = SemanticGraphPatchFragment(
        nodes=[
            SemanticGraphNode(
                temp_id=canonical_key,
                class_name="other",
                properties=[_fact("code", "A")],
            )
        ],
        edges=[],
        coverage=_coverage(),
        warnings=[],
    )

    result = GraphPatchCompiler().compile(
        fragment,
        projection,
        staged_entities={
            f"entity:{canonical_key}": {
                "stableKey": canonical_key,
                "className": "record",
                "identity": {"code": "A"},
            }
        },
    )

    assert [issue.code for issue in result.issues] == [
        "CANONICAL_REFERENCE_CLASS_MISMATCH"
    ]


def test_unknown_stable_temp_id_is_a_canonical_reference_error() -> None:
    projection = _projection(["code"])
    unknown_key = "a" * 64
    fragment = SemanticGraphPatchFragment(
        nodes=[
            SemanticGraphNode(
                temp_id=unknown_key,
                class_name="record",
                properties=[_fact("code", "A")],
            )
        ],
        edges=[],
        coverage=_coverage(),
        warnings=[],
    )

    result = GraphPatchCompiler().compile(fragment, projection)

    assert [issue.code for issue in result.issues] == [
        "UNKNOWN_CANONICAL_REFERENCE"
    ]
