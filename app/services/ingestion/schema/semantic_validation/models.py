"""Shared value objects for the Semantic Evidence Gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    """Kết luận về việc evidence có support claim hay không."""

    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    NEI = "NEI"  # not enough information
    UNCERTAIN = "UNCERTAIN"


class RuleOutcome(str, Enum):
    PASS = "PASS"
    CONTRADICTION = "CONTRADICTION"
    NOT_APPLICABLE = "NOT_APPLICABLE"


# Error codes (tách khỏi EVIDENCE_NOT_GROUNDED của grounding).
CODE_REFUTED = "EVIDENCE_SEMANTIC_REFUTED"
CODE_NEI = "EVIDENCE_SEMANTIC_NEI"
CODE_UNCERTAIN = "EVIDENCE_SEMANTIC_UNCERTAIN"
CODE_NUMERIC = "EVIDENCE_NUMERIC_MISMATCH"
CODE_UNIT = "EVIDENCE_UNIT_MISMATCH"
CODE_RANGE = "EVIDENCE_RANGE_MISMATCH"
CODE_NEGATION = "EVIDENCE_NEGATION_CONTRADICTION"
CODE_RELATION = "EVIDENCE_RELATION_MISMATCH"

# Tăng khi đổi rule / claim template để vô hiệu hoá cache NLI.
RULE_VERSION = "1"


@dataclass(frozen=True)
class Claim:
    """Một fact (property hoặc edge) đã chuẩn hoá thành câu khẳng định."""

    kind: str  # "property" | "edge"
    location: str
    text: str
    predicate: str
    predicate_label: str
    subject_names: tuple[str, ...] = ()
    object_names: tuple[str, ...] = ()
    value: Any = None
    relation_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceItem:
    location: str
    chunk_index: int
    text: str


@dataclass(frozen=True)
class ClaimUnit:
    claim: Claim
    evidence: tuple[EvidenceItem, ...]


@dataclass(frozen=True)
class RuleResult:
    rule: str
    outcome: RuleOutcome
    code: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class NliScores:
    entailment: float
    neutral: float
    contradiction: float


@dataclass
class Finding:
    claim: Claim
    verdict: Verdict
    code: str | None = None
    source: str = "nli"  # rule | nli | llm | unavailable
    detail: str = ""
    rule_results: list[RuleResult] = field(default_factory=list)
    scores: NliScores | None = None
    evidence_text: str = ""
