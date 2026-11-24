"""Phase 1: canonical claim builder."""

from datetime import date
from decimal import Decimal

import pytest

from app.schemas.ingestion.graph_patch import GraphEdge, GraphNode, GraphPatchFragment
from app.schemas.ingestion.grounding import Evidence, PropertyFact
from app.services.ingestion.schema.semantic_validation import (
    LabelCatalog,
    build_claims,
    build_edge_claim,
    build_property_claim,
)
from app.services.ingestion.schema.semantic_validation.claim_builder import (
    format_value,
    humanize,
)

LABELS = LabelCatalog(
    classes={"belt": "Đai", "coach": "Huấn luyện viên", "class": "lớp"},
    properties={"belt.minimum_age": "độ tuổi tối thiểu", "belt.auto_renew": "tự động gia hạn"},
    units={"belt.minimum_age": "tuổi"},
    relations={"COACHES": "phụ trách"},
)


def _prop(value, name="minimum_age", labels=LABELS, identity=None):
    return build_property_claim(
        class_name="belt",
        identity=identity or {"name": "đỏ"},
        property_name=name,
        value=value,
        location="nodes.0.properties.0",
        labels=labels,
    )


def test_property_with_unit_matches_spec_example() -> None:
    assert _prop(12).text == "Đai đỏ có độ tuổi tối thiểu là 12 tuổi."


def test_deterministic() -> None:
    assert {_prop(12).text for _ in range(50)} == {_prop(12).text}


@pytest.mark.parametrize(
    "value,expected",
    [
        (500000, "500.000"),
        (9999, "9999"),
        (1.5, "1,5"),
        (Decimal("2.50"), "2,5"),
        (-3, "-3"),
        ("  chữ  ", "chữ"),
        ("2026-03-05", "05/03/2026"),
        (date(2026, 3, 5), "05/03/2026"),
        (["a", "b"], "a, b"),
        ({"min": 3, "max": 5}, "từ 3 đến 5"),
        (True, "có"),
        (False, "không"),
    ],
)
def test_format_value(value, expected) -> None:
    assert format_value(value) == expected


@pytest.mark.parametrize("value", [None, "", "   ", [], {}])
def test_empty_values_build_no_claim(value) -> None:
    assert _prop(value) is None


def test_boolean_claims() -> None:
    assert _prop(True, "auto_renew").text == "Đai đỏ có tự động gia hạn."
    assert _prop(False, "auto_renew").text == "Đai đỏ không có tự động gia hạn."


def test_fallback_to_humanized_labels_without_catalog() -> None:
    claim = _prop("x", "minimumAge", labels=None)
    assert claim.text == "belt đỏ có minimum age là x."


def test_subject_not_prefixed_twice() -> None:
    assert _prop(1, identity={"name": "Đai vàng"}).text.startswith("Đai vàng có")


def test_name_like_identity_key_first() -> None:
    claim = _prop(1, identity={"code": "B1", "ten": "đỏ"})
    assert claim.subject_names == ("đỏ", "B1")


def test_unicode_vietnamese_preserved() -> None:
    assert "Nâng cao" in _prop("Nâng cao", "level").text


def test_edge_claim() -> None:
    claim = build_edge_claim(
        edge_name="COACHES",
        source_class="coach",
        source_identity={"name": "A"},
        source_fallback="n1",
        target_class="class",
        target_identity={"name": "Nâng cao"},
        target_fallback="n2",
        location="edges.0",
        labels=LABELS,
    )
    assert claim.text == "Huấn luyện viên A phụ trách lớp Nâng cao."
    assert claim.subject_names == ("A",) and claim.object_names == ("Nâng cao",)
    assert "phụ trách" in claim.relation_terms


def test_humanize() -> None:
    assert humanize("minimumAge") == "minimum age"
    assert humanize("MIN_AGE") == "min age"


def test_build_claims_covers_properties_and_edges() -> None:
    ev = [Evidence(chunk_index=0, text="Đai đỏ tối thiểu 12 tuổi.")]
    fragment = GraphPatchFragment(
        nodes=[
            GraphNode(
                temp_id="n1",
                class_name="belt",
                identity={"name": "đỏ"},
                properties=[
                    PropertyFact(property_name="minimum_age", value=12, evidence=ev),
                    PropertyFact(property_name="note", value=None, evidence=ev),
                ],
            ),
            GraphNode(temp_id="n2", class_name="coach", identity={"name": "A"}),
        ],
        edges=[
            GraphEdge(edge_name="COACHES", source_temp_id="n2", target_temp_id="n1", evidence=ev),
            GraphEdge(edge_name="COACHES", source_temp_id="n2", target_temp_id="zz", evidence=ev),
        ],
    )
    units = build_claims(fragment, LABELS)
    assert [u.claim.location for u in units] == ["nodes.0.properties.0", "edges.0"]
    assert units[0].evidence[0].location == "nodes.0.properties.0.evidence.0"
