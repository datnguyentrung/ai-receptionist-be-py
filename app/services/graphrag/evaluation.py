"""Deterministic retrieval and grounding metrics for the Taekwondo eval set."""

from __future__ import annotations

from typing import Any


def evaluate_cases(
    cases: list[dict[str, Any]], results: dict[str, dict[str, Any]]
) -> dict[str, float | int]:
    """Score retrieval outputs without asking an LLM to judge its own answer."""

    answerable = [case for case in cases if not case.get("shouldAbstain", False)]
    recall_total = 0.0
    reciprocal_rank_total = 0.0
    abstention_correct = 0
    citation_correct = 0
    citation_total = 0
    grounded_claims = 0
    claim_total = 0

    for case in cases:
        result = results.get(case["id"], {})
        passages = list(result.get("passages") or [])[:10]
        retrieved_sources = [
            item.get("documentName") or (item.get("source") or {}).get("documentName")
            for item in passages
        ]
        expected_sources = set(case.get("expectedSources") or [])
        should_abstain = bool(case.get("shouldAbstain", False))
        did_abstain = not bool(result.get("sufficientEvidence", False))
        abstention_correct += int(should_abstain == did_abstain)

        if not should_abstain:
            hits = expected_sources.intersection(retrieved_sources)
            recall_total += len(hits) / len(expected_sources) if expected_sources else 0.0
            first_rank = next(
                (
                    rank
                    for rank, source in enumerate(retrieved_sources, start=1)
                    if source in expected_sources
                ),
                None,
            )
            reciprocal_rank_total += 1.0 / first_rank if first_rank else 0.0

        passage_ids = {
            item.get("chunkId") or (item.get("source") or {}).get("chunkId")
            for item in passages
        }
        for citation in result.get("citationChunkIds") or []:
            citation_total += 1
            citation_correct += int(citation in passage_ids)
        for claim in result.get("claims") or []:
            claim_total += 1
            sources = set(claim.get("sourceChunkIds") or [])
            grounded_claims += int(bool(sources) and sources.issubset(passage_ids))

    answerable_count = len(answerable)
    return {
        "caseCount": len(cases),
        "answerableCount": answerable_count,
        "recallAt10": recall_total / answerable_count if answerable_count else 0.0,
        "mrr": reciprocal_rank_total / answerable_count if answerable_count else 0.0,
        "citationCorrectness": (
            citation_correct / citation_total if citation_total else 1.0
        ),
        "answerFaithfulness": grounded_claims / claim_total if claim_total else 1.0,
        "abstentionAccuracy": abstention_correct / len(cases) if cases else 0.0,
    }


def release_gate(metrics: dict[str, float | int]) -> dict[str, Any]:
    checks = {
        "recallAt10": float(metrics["recallAt10"]) >= 0.90,
        "citationCorrectness": float(metrics["citationCorrectness"]) == 1.0,
        "answerFaithfulness": float(metrics["answerFaithfulness"]) == 1.0,
        "abstentionAccuracy": float(metrics["abstentionAccuracy"]) >= 0.95,
    }
    return {"passed": all(checks.values()), "checks": checks}


__all__ = ["evaluate_cases", "release_gate"]
