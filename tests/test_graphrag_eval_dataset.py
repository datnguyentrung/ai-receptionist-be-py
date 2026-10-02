import json
from pathlib import Path


def test_eval_dataset_has_required_coverage() -> None:
    path = Path(__file__).parents[1] / "docs" / "evaluation" / "taekwondo-rag-eval.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert len(cases) >= 30
    assert len({case["id"] for case in cases}) == len(cases)
    categories = {case["category"] for case in cases}
    assert {"fact", "multi-hop", "multi-document", "ambiguous", "unanswerable"} <= categories
    assert sum(case["shouldAbstain"] for case in cases) >= 5
