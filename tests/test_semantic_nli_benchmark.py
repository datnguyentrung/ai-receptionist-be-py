"""Benchmark NLI thật (mDeBERTa). Bỏ qua mặc định: RUN_NLI_MODEL_TESTS=1 pytest tests/test_semantic_nli_benchmark.py -s

Metric quan trọng nhất: False Acceptance (REFUTED/NEI bị gán SUPPORTED), không chỉ accuracy.
"""

import os

import pytest

from app.core.config import settings
from app.services.ingestion.schema.semantic_validation import (
    DecisionPolicy,
    NliPair,
    TransformersNliVerifier,
    Verdict,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_NLI_MODEL_TESTS") != "1", reason="needs model download"
)

S, R, N = Verdict.SUPPORTED, Verdict.REFUTED, Verdict.NEI

CASES = [
    # (evidence, claim, expected)
    ("Đai đỏ có độ tuổi tối thiểu là 12 tuổi.", "Đai đỏ có độ tuổi tối thiểu là 12 tuổi.", S),
    ("Học viên đai đỏ phải từ 12 tuổi trở lên.", "Đai đỏ có độ tuổi tối thiểu là 12 tuổi.", S),
    ("Gói Standard có học phí 500.000 đồng/tháng.", "Gói Standard có học phí là 500.000 đồng.", S),
    ("Huấn luyện viên A phụ trách lớp Nâng cao.", "Huấn luyện viên A phụ trách lớp Nâng cao.", S),
    ("Lớp Nâng cao do Huấn luyện viên A phụ trách.", "Huấn luyện viên A phụ trách lớp Nâng cao.", S),
    ("Gói Standard không tự động gia hạn.", "Gói Standard không có tự động gia hạn.", S),
    ("Gói Standard có học phí 500.000 đồng/tháng.", "Gói Premium có học phí là 500.000 đồng.", R),
    ("Đai đỏ có độ tuổi tối thiểu là 12 tuổi.", "Đai đỏ có độ tuổi tối thiểu là 10 tuổi.", R),
    ("Gói Standard không tự động gia hạn.", "Gói Standard có tự động gia hạn.", R),
    # Đảo chiều quan hệ: NLI thật gán SUPPORTED (đã đo) -> do relation_rule chặn, xem test_semantic_rules.
    ("Huấn luyện viên A phụ trách lớp Nâng cao.", "Huấn luyện viên B phụ trách lớp Nâng cao.", R),
    ("Học viên được bảo lưu tối đa 3 tháng.", "Học viên được bảo lưu tối đa 6 tháng.", R),
    ("Phòng tập mở cửa từ 6 giờ sáng.", "Phòng tập có phí gửi xe là 5.000 đồng.", N),
    ("Câu lạc bộ có nhiều lớp học cho trẻ em.", "Lớp Nâng cao có học phí là 800.000 đồng.", N),
    ("Đai đỏ là cấp đai trước đai đen.", "Đai đỏ có độ tuổi tối thiểu là 12 tuổi.", N),
    ("Huấn luyện viên A có nhiều năm kinh nghiệm.", "Huấn luyện viên A phụ trách lớp Nâng cao.", N),
]


def test_nli_benchmark_false_acceptance_zero() -> None:
    nli = TransformersNliVerifier(
        settings.INGESTION_NLI_MODEL, device=settings.INGESTION_NLI_DEVICE
    )
    policy = DecisionPolicy()
    scores = nli.verify_batch([NliPair(e, c) for e, c, _ in CASES])
    verdicts = [policy.decide(s) for s in scores]
    for (evidence, claim, expected), verdict, s in zip(CASES, verdicts, scores):
        print(f"{expected.value:9s} got {verdict.value:9s} e={s.entailment:.2f} "
              f"n={s.neutral:.2f} c={s.contradiction:.2f} | {claim}")
    false_accept = [
        c for (e, c, exp), v in zip(CASES, verdicts) if exp is not S and v is S
    ]
    assert not false_accept, false_accept
