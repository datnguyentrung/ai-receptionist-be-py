from app.services.graphrag.evaluation import evaluate_cases, release_gate


def test_evaluation_metrics_and_release_gate() -> None:
    cases = [
        {"id": "answerable", "expectedSources": ["rules.pdf"], "shouldAbstain": False},
        {"id": "outside", "expectedSources": [], "shouldAbstain": True},
    ]
    results = {
        "answerable": {
            "sufficientEvidence": True,
            "passages": [{"chunkId": "c1", "documentName": "rules.pdf"}],
            "citationChunkIds": ["c1"],
            "claims": [{"sourceChunkIds": ["c1"]}],
        },
        "outside": {"sufficientEvidence": False, "passages": []},
    }
    metrics = evaluate_cases(cases, results)
    assert metrics["recallAt10"] == 1.0
    assert metrics["mrr"] == 1.0
    assert metrics["citationCorrectness"] == 1.0
    assert metrics["answerFaithfulness"] == 1.0
    assert metrics["abstentionAccuracy"] == 1.0
    assert release_gate(metrics)["passed"] is True
