"""Phase 2: deterministic semantic rules (chỉ REJECT khi chắc chắn)."""

import pytest

from app.services.ingestion.schema.semantic_validation.models import Claim, RuleOutcome
from app.services.ingestion.schema.semantic_validation.rules import (
    negation_rule,
    numeric_rule,
    range_rule,
    relation_rule,
    run_rules,
)

C, P, N = RuleOutcome.CONTRADICTION, RuleOutcome.PASS, RuleOutcome.NOT_APPLICABLE


def prop(value, label="độ tuổi tối thiểu", predicate="minimum_age", names=("đỏ",)) -> Claim:
    return Claim(
        kind="property", location="nodes.0.properties.0", text="x",
        predicate=predicate, predicate_label=label, subject_names=names, value=value,
    )


def edge(source="A", target="B", terms=("quản lý",)) -> Claim:
    return Claim(
        kind="edge", location="edges.0", text="x", predicate="MANAGES",
        predicate_label="quản lý", subject_names=(source,), object_names=(target,),
        relation_terms=terms,
    )


# ------------------------------------------------------------------ numeric
@pytest.mark.parametrize(
    "value,text,expected",
    [
        (12, "Độ tuổi tối thiểu là 12.", P),
        (10, "Độ tuổi tối thiểu là 12.", C),
        (500000, "Học phí 500.000 đồng", P),
        (500000, "Học phí 500,000 đồng", P),
        (500000, "Học phí 500000 đồng", P),
        (500000, "Học phí 500 nghìn đồng", P),
        (1500000, "Học phí 1,5 triệu đồng", P),
        (1500000, "Học phí 1.500.000 đ", P),
        (600000, "Học phí 500.000 đồng", C),
        (0.2, "Giảm 20%", P),
        (20, "Giảm 20 phần trăm", P),
        (30, "Giảm 20%", C),
        (1, "Nặng 1000 g", P),
        (1000, "Nặng 1 kg", P),
        (2, "Nặng 1000 g", C),
        (12.5, "Giá 12,5", P),
        (12, "Độ tuổi tối thiểu là mười hai", N),
        (12, "Không có số", N),
        ("12", "12 tuổi", N),
        (True, "12 tuổi", N),
        (12, "Từ 12 đến 15 tuổi", P),
    ],
)
def test_numeric(value, text, expected) -> None:
    assert numeric_rule(prop(value), text).outcome is expected


def test_numeric_ignores_edges() -> None:
    assert numeric_rule(edge(), "12").outcome is N


# -------------------------------------------------------------------- range
@pytest.mark.parametrize(
    "claim_value,text,expected",
    [
        (12, "Độ tuổi tối thiểu là 12", P),
        (12, "Học viên phải ít nhất 12 tuổi", P),
        (12, "Từ 12 tuổi trở lên", P),
        (12, "Độ tuổi ≥ 12", P),
        (12, "Độ tuổi >= 12", P),
        (12, "Tuổi trên 12", C),
        (12, "Tuổi tối đa 12", C),
        (12, "Từ 12 tuổi trở xuống", C),
        (12, "Tuổi dưới 12", C),
        (12, "Từ 12 đến 15 tuổi", P),
        (12, "Chỉ nhắc đến 12 tuổi", N),
        (12, "Tuổi tối thiểu 15", N),
        (">12", "Tuổi từ 12 tuổi trở lên", C),
        (">12", "Tuổi trên 12", P),
        ("≥12", "Tuổi trên 12", C),
        ("<=5", "Không quá 5 người", P),
    ],
)
def test_range_min_hint(claim_value, text, expected) -> None:
    claim = prop(claim_value, label="độ tuổi tối thiểu" if isinstance(claim_value, int) else "x")
    assert range_rule(claim, text).outcome is expected


def test_range_max_hint_direction() -> None:
    claim = prop(30, label="sĩ số tối đa", predicate="max_students")
    assert range_rule(claim, "Mỗi lớp tối đa 30 học viên").outcome is P
    assert range_rule(claim, "Mỗi lớp ít nhất 30 học viên").outcome is C


def test_range_without_comparator_hint_not_applicable() -> None:
    assert range_rule(prop(12, label="tuổi", predicate="age"), "Trên 12 tuổi").outcome is N


# ----------------------------------------------------------------- negation
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Không tự động gia hạn.", C),
        ("Gói này không được tự động gia hạn khi hết hạn", C),
        ("Cấm tự động gia hạn", C),
        ("Tự động gia hạn: không", C),
        ("Chưa hỗ trợ tự động gia hạn", C),
        ("Gói có tự động gia hạn hằng tháng", N),
        ("Không chỉ tự động gia hạn mà còn giảm giá", N),
        ("Học phí 500.000 đồng", N),
        ("Không tính phí. Có tự động gia hạn", N),
        ("Không nhận thanh toán tiền mặt, tự động gia hạn hằng tháng", N),
    ],
)
def test_negation_true_claim(text, expected) -> None:
    claim = prop(True, label="tự động gia hạn", predicate="auto_renew")
    assert negation_rule(claim, text).outcome is expected


@pytest.mark.parametrize("value", [False, "có", 1, None])
def test_negation_only_for_true(value) -> None:
    claim = prop(value, label="tự động gia hạn")
    assert negation_rule(claim, "Không tự động gia hạn").outcome is N


def test_negation_allowed_vs_not_allowed() -> None:
    claim = prop(True, label="cho phép bảo lưu", predicate="allow_reserve")
    assert negation_rule(claim, "Không cho phép bảo lưu").outcome is C
    assert negation_rule(claim, "Học viên được cho phép bảo lưu").outcome is N


# ----------------------------------------------------------------- relation
@pytest.mark.parametrize(
    "text,expected",
    [
        ("A quản lý B", P),
        ("B quản lý A", C),
        ("B được A quản lý", P),
        ("A được B quản lý", C),
        ("A và B", N),
        ("A quản lý C", N),
        ("quản lý A B", N),
    ],
)
def test_relation_direction(text, expected) -> None:
    assert relation_rule(edge(), text).outcome is expected


def test_relation_without_terms_not_applicable() -> None:
    assert relation_rule(edge(terms=()), "B quản lý A").outcome is N


def test_relation_case_insensitive_and_alias() -> None:
    claim = edge("Huấn luyện viên A", "Lớp Nâng cao", terms=("phụ trách", "dạy"))
    assert relation_rule(claim, "Lớp nâng cao do Huấn luyện viên A phụ trách.").outcome is P
    assert relation_rule(claim, "Lớp Nâng cao dạy Huấn luyện viên A").outcome is C


# --------------------------------------------------------------- run_rules
def test_run_rules_reports_all_rules() -> None:
    results = run_rules(prop(10), "Độ tuổi tối thiểu là 12.")
    assert {r.rule for r in results} == {"numeric", "range", "negation", "relation"}
    assert [r.code for r in results if r.outcome is C] == ["EVIDENCE_NUMERIC_MISMATCH"]
