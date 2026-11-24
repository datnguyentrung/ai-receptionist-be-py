"""SemanticEvidenceGate: Claim -> Rules -> NLI -> Policy -> LLM fallback.

Interface nhỏ: `await gate.verify(fragment, chunks, labels) -> SemanticReport`.
Hai seam bên ngoài: `NliVerifier` (nli.py) và `LlmFallback` (ở đây).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol, Sequence

from app.schemas.ingestion.coverage import PreparedChunk, ValidationIssue
from app.schemas.ingestion.graph_patch import GraphPatchFragment
from app.services.ingestion.schema.semantic_validation.claim_builder import (
    LabelCatalog,
    build_claims,
)
from app.services.ingestion.schema.semantic_validation.decision_policy import (
    DecisionPolicy,
    aggregate,
)
from app.services.ingestion.schema.semantic_validation.models import (
    CODE_NEI,
    CODE_REFUTED,
    CODE_UNCERTAIN,
    Claim,
    ClaimUnit,
    Finding,
    NliScores,
    Verdict,
)
from app.services.ingestion.schema.semantic_validation.nli import (
    NliPair,
    NliUnavailableError,
    NliVerifier,
)
from app.services.ingestion.schema.semantic_validation.rules import (
    first_contradiction,
    run_rules,
)

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
_TOKEN = re.compile(r"\w+")
_WHITESPACE = re.compile(r"\s+")


# ------------------------------------------------------------------ fallback
@dataclass(frozen=True)
class FallbackRequest:
    key: str  # claim.location
    claim: Claim
    evidence_text: str
    source_text: str
    chunk_index: int
    reason: str


@dataclass(frozen=True)
class FallbackVerdict:
    verdict: Verdict
    source_quote: str | None = None
    explanation: str = ""


class LlmFallback(Protocol):
    async def judge(
        self, requests: Sequence[FallbackRequest]
    ) -> Mapping[str, FallbackVerdict]: ...


# --------------------------------------------------------------------- report
@dataclass
class SemanticReport:
    findings: list[Finding]
    nli_available: bool = True

    @property
    def unsupported(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict is not Verdict.SUPPORTED]

    def to_issues(self, *, block_uncertain: bool = True) -> list[ValidationIssue]:
        issues = []
        for finding in self.unsupported:
            if finding.verdict in {Verdict.NEI, Verdict.UNCERTAIN} and not block_uncertain:
                continue
            issues.append(
                ValidationIssue(
                    code=finding.code or CODE_UNCERTAIN,
                    message=(
                        f"Evidence does not support claim {finding.claim.text!r} "
                        f"[{finding.verdict.value}/{finding.source}] {finding.detail}"
                    ).strip(),
                    location=finding.claim.location,
                    retryable=True,
                )
            )
        return issues

    def summary(self) -> list[dict[str, Any]]:
        return [
            {
                "location": f.claim.location,
                "claim": f.claim.text,
                "verdict": f.verdict.value,
                "code": f.code,
                "source": f.source,
                "rules": [r.rule for r in f.rule_results if r.outcome.value != "NOT_APPLICABLE"],
                "nli": (
                    {
                        "e": round(f.scores.entailment, 3),
                        "n": round(f.scores.neutral, 3),
                        "c": round(f.scores.contradiction, 3),
                    }
                    if f.scores
                    else None
                ),
            }
            for f in self.unsupported
        ]


# --------------------------------------------------------------------- windows
def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.casefold()) if len(t) >= 2}


def evidence_windows(claim: Claim, text: str, *, max_windows: int = 3, short: int = 1200) -> list[str]:
    """Evidence ngắn -> nguyên văn; dài -> bản cắt đầu + các câu/dòng liên quan nhất tới claim."""
    text = text.strip()
    if len(text) <= short:
        return [text]
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    if len(sentences) <= 1:
        return [text[:short * 2]]
    wanted = _tokens(claim.text)
    ranked = sorted(
        range(len(sentences)),
        key=lambda i: (-len(wanted & _tokens(sentences[i])), i),
    )[:max_windows]
    windows = [sentences[i] for i in sorted(ranked)]
    head = text[:short]
    if head not in windows:
        windows.append(head)
    return windows


def _same_quote(quote: str, haystack: str) -> bool:
    norm = lambda s: _WHITESPACE.sub(" ", s).strip().casefold()  # noqa: E731
    return bool(quote.strip()) and norm(quote) in norm(haystack)


# ------------------------------------------------------------------------ gate
class SemanticEvidenceGate:
    def __init__(
        self,
        *,
        nli: NliVerifier | None,
        policy: DecisionPolicy | None = None,
        llm_fallback: LlmFallback | None = None,
        enforce: bool = True,
        block_uncertain: bool = True,
        max_windows: int = 3,
    ) -> None:
        self._nli = nli
        self._policy = policy or DecisionPolicy()
        self._llm = llm_fallback
        self.enforce = enforce
        self.block_uncertain = block_uncertain
        self._max_windows = max_windows

    async def verify(
        self,
        fragment: GraphPatchFragment,
        chunks: Iterable[PreparedChunk],
        labels: LabelCatalog | None = None,
    ) -> SemanticReport:
        units = build_claims(fragment, labels)
        chunk_text = {chunk.chunk_index: chunk.text for chunk in chunks}

        # 1. rules + gom NLI pairs (một batch cho cả fragment)
        evidence_verdicts: list[list[Verdict]] = [[] for _ in units]
        rule_logs: list[list] = [[] for _ in units]
        rule_codes: list[list[str]] = [[] for _ in units]
        pending: list[tuple[int, list[int]]] = []  # (unit idx, pair indices)
        pairs: list[NliPair] = []
        pair_index: dict[tuple[str, str], int] = {}
        for u_idx, unit in enumerate(units):
            for item in unit.evidence:
                results = run_rules(unit.claim, item.text)
                rule_logs[u_idx].extend(results)
                hit = first_contradiction(results)
                if hit is not None:
                    evidence_verdicts[u_idx].append(Verdict.REFUTED)
                    rule_codes[u_idx].append(hit.code or CODE_REFUTED)
                    continue
                indices = []
                for window in evidence_windows(unit.claim, item.text, max_windows=self._max_windows):
                    key = (window, unit.claim.text)
                    if key not in pair_index:
                        pair_index[key] = len(pairs)
                        pairs.append(NliPair(window, unit.claim.text))
                    indices.append(pair_index[key])
                pending.append((u_idx, indices))
            if len(unit.evidence) > 1 and not rule_codes[u_idx]:
                combined_text = "\n".join(i.text for i in unit.evidence)
                combined_rules = run_rules(unit.claim, combined_text)
                if first_contradiction(combined_rules) is None:
                    indices = []
                    for window in evidence_windows(
                        unit.claim, combined_text, max_windows=self._max_windows
                    ):
                        key = (window, unit.claim.text)
                        if key not in pair_index:
                            pair_index[key] = len(pairs)
                            pairs.append(NliPair(window, unit.claim.text))
                        indices.append(pair_index[key])
                    pending.append((u_idx, indices))

        # 2. NLI (blocking inference -> thread)
        scores: list[NliScores] = []
        nli_available = True
        if pairs:
            if self._nli is None:
                nli_available = False
            else:
                try:
                    scores = await asyncio.to_thread(self._nli.verify_batch, pairs)
                except NliUnavailableError as exc:
                    nli_available = False
                    logger.error("SEMANTIC_GATE_NLI_UNAVAILABLE error=%s", exc)
                except Exception:  # noqa: BLE001 - fail closed, never skip validation
                    nli_available = False
                    logger.exception("SEMANTIC_GATE_NLI_ERROR")
        best_scores: dict[int, NliScores] = {}
        for u_idx, indices in pending:
            if not nli_available:
                evidence_verdicts[u_idx].append(Verdict.UNCERTAIN)
                continue
            window_verdicts = [self._policy.decide(scores[i]) for i in indices]
            evidence_verdicts[u_idx].append(aggregate(window_verdicts))
            pick = max(indices, key=lambda i: scores[i].entailment)
            if u_idx not in best_scores or scores[pick].entailment > best_scores[u_idx].entailment:
                best_scores[u_idx] = scores[pick]

        # 3. gộp theo claim
        findings: list[Finding] = []
        for u_idx, unit in enumerate(units):
            verdict = aggregate(evidence_verdicts[u_idx])
            by_rule = Verdict.REFUTED in evidence_verdicts[u_idx] and bool(rule_codes[u_idx])
            findings.append(
                Finding(
                    claim=unit.claim,
                    verdict=verdict,
                    code=self._code(verdict, rule_codes[u_idx] if by_rule else []),
                    source="rule" if by_rule and verdict is Verdict.REFUTED else (
                        "nli" if nli_available else "unavailable"
                    ),
                    detail="" if nli_available else "NLI unavailable",
                    rule_results=rule_logs[u_idx],
                    scores=best_scores.get(u_idx),
                    evidence_text="\n".join(i.text for i in unit.evidence),
                )
            )

        # 4. LLM fallback chỉ cho NEI / UNCERTAIN
        await self._fallback(units, findings, chunk_text)
        return SemanticReport(findings, nli_available)

    @staticmethod
    def _code(verdict: Verdict, rule_codes: list[str]) -> str | None:
        if verdict is Verdict.REFUTED:
            return rule_codes[0] if rule_codes else CODE_REFUTED
        return {Verdict.NEI: CODE_NEI, Verdict.UNCERTAIN: CODE_UNCERTAIN}.get(verdict)

    async def _fallback(
        self,
        units: list[ClaimUnit],
        findings: list[Finding],
        chunk_text: Mapping[int, str],
    ) -> None:
        if self._llm is None:
            return
        requests: list[FallbackRequest] = []
        targets: dict[str, Finding] = {}
        for unit, finding in zip(units, findings):
            if finding.verdict not in {Verdict.NEI, Verdict.UNCERTAIN}:
                continue
            chunk_idx = unit.evidence[0].chunk_index
            chunk_indices = list(dict.fromkeys(item.chunk_index for item in unit.evidence))
            source_text = "\n\n".join(
                chunk_text[idx]
                for idx in chunk_indices
                if idx in chunk_text and chunk_text[idx]
            )
            requests.append(
                FallbackRequest(
                    key=unit.claim.location,
                    claim=unit.claim,
                    evidence_text=finding.evidence_text,
                    source_text=source_text,
                    chunk_index=chunk_idx,
                    reason=finding.detail or finding.verdict.value,
                )
            )
            targets[unit.claim.location] = finding
        if not requests:
            return
        try:
            decisions = await self._llm.judge(requests)
        except Exception:  # noqa: BLE001 - fail closed: giữ nguyên NEI/UNCERTAIN
            logger.exception("SEMANTIC_GATE_LLM_FALLBACK_ERROR")
            return
        for request in requests:
            decision = decisions.get(request.key)
            finding = targets[request.key]
            if decision is None:
                continue
            if decision.verdict is Verdict.SUPPORTED:
                quote = decision.source_quote or ""
                if _same_quote(quote, request.evidence_text) or _same_quote(quote, request.source_text):
                    finding.verdict, finding.code, finding.source = Verdict.SUPPORTED, None, "llm"
                else:  # Python re-check: LLM quote phải tồn tại trong nguồn
                    finding.detail = "LLM sourceQuote not found in source; result rejected"
                continue
            if decision.verdict is Verdict.REFUTED:
                finding.verdict, finding.code, finding.source = Verdict.REFUTED, CODE_REFUTED, "llm"
                finding.detail = decision.explanation
