"""Evaluate captured GraphRAG outputs against the deterministic 30-case suite."""

import argparse
import json
from pathlib import Path

from app.services.graphrag.evaluation import evaluate_cases, release_gate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path, help="JSON object keyed by eval case id")
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("docs/evaluation/taekwondo-rag-eval.json"),
    )
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    results = json.loads(args.results.read_text(encoding="utf-8"))
    metrics = evaluate_cases(cases, results)
    report = {"metrics": metrics, "releaseGate": release_gate(metrics)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["releaseGate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
