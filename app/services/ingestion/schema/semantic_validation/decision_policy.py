"""Decision policy: NLI scores -> Verdict, và gộp nhiều verdict thành một."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.services.ingestion.schema.semantic_validation.models import (
    NliScores,
    Verdict,
)


@dataclass(frozen=True)
class DecisionPolicy:
    support_threshold: float = 0.90
    refute_threshold: float = 0.90
    minimum_margin: float = 0.20
    nei_threshold: float = 0.60

    def decide(self, scores: NliScores) -> Verdict:
        e, n, c = scores.entailment, scores.neutral, scores.contradiction
        if e >= self.support_threshold and e - max(n, c) >= self.minimum_margin:
            return Verdict.SUPPORTED
        if c >= self.refute_threshold and c - max(e, n) >= self.minimum_margin:
            return Verdict.REFUTED
        if n >= self.nei_threshold and n - max(e, c) >= self.minimum_margin:
            return Verdict.NEI
        return Verdict.UNCERTAIN


def aggregate(verdicts: Iterable[Verdict]) -> Verdict:
    """Nhiều evidence/window cho một claim.

    SUPPORTED + không REFUTED -> SUPPORTED; SUPPORTED + REFUTED -> UNCERTAIN (conflict);
    chỉ REFUTED -> REFUTED; còn lại UNCERTAIN nếu có, ngược lại NEI.
    """
    items = set(verdicts)
    if Verdict.SUPPORTED in items:
        return Verdict.UNCERTAIN if Verdict.REFUTED in items else Verdict.SUPPORTED
    if Verdict.REFUTED in items:
        return Verdict.REFUTED
    if Verdict.UNCERTAIN in items or not items:
        return Verdict.UNCERTAIN
    return Verdict.NEI
