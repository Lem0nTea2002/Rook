from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


_SOURCE_HASHES = {
    "reports/baseline.json": "cabfd1e8ab14fb808ac5a590aebda18199e9f9dc1957955f51567b3bc388edbe",
    "reports/rerank.json": "f17768435d20207b87960f7c045cfd98b5ade3158cefa46a1d7150d9ee24051b",
    "reports/answer.json": "efb551efc1d8027df7930dd512eb85b993a0f1de15551c0e2fd87078045ba5ab",
}


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_unchanged(root: Path, relative: str) -> bool:
    return hashlib.sha256((root / relative).read_bytes()).hexdigest() == _SOURCE_HASHES[
        relative
    ]


def _files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def validate_compare(root: Path) -> str | None:
    expected_files = {
        "reports/baseline.json",
        "reports/rerank.json",
        "evaluation-summary.json",
    }
    if _files(root) != expected_files:
        return "unexpected_files"
    if not all(_source_unchanged(root, item) for item in expected_files if item.startswith("reports/")):
        return "source_modified"
    payload = _load(root / "evaluation-summary.json")
    expected_dataset = (
        "/home/suity/worksapce/PycharmProjects/FindJob/"
        "agentic-rag-for-dummies-main/project/evaluation/datasets/quick_eval_5.jsonl"
    )
    if payload.get("schema") != "rook.rag-eval-summary/v1":
        return "schema"
    if payload.get("dataset_path") != expected_dataset or payload.get("case_count") != 5:
        return "dataset_identity"
    modes = {item.get("mode"): item for item in payload.get("modes", [])}
    expected = {
        "baseline_hybrid": (1.0, 1.0, 14.92, 18.12),
        "hybrid_rerank": (1.0, 1.0, 6991.2, 34872.52),
    }
    if set(modes) != set(expected):
        return "modes"
    for name, values in expected.items():
        item = modes[name]
        observed = (
            item.get("source_hit_rate"),
            item.get("mrr"),
            item.get("avg_latency_ms"),
            item.get("p95_latency_ms"),
        )
        if observed != values:
            return f"metrics_{name}"
    comparison = payload.get("comparison", {})
    if comparison.get("quality_winner") != "tie":
        return "quality_winner"
    if comparison.get("latency_winner") != "baseline_hybrid":
        return "latency_winner"
    if comparison.get("p95_latency_delta_ms") != 34854.4:
        return "latency_delta"
    caveats = payload.get("caveats", [])
    if not isinstance(caveats, list) or not caveats:
        return "caveats"
    return None


def validate_skipped(root: Path) -> str | None:
    expected_files = {"reports/answer.json", "evaluation-summary.json"}
    if _files(root) != expected_files:
        return "unexpected_files"
    if not _source_unchanged(root, "reports/answer.json"):
        return "source_modified"
    payload = _load(root / "evaluation-summary.json")
    expected_dataset = (
        "/home/suity/worksapce/PycharmProjects/FindJob/"
        "agentic-rag-for-dummies-main/project/evaluation/datasets/sample_eval.jsonl"
    )
    if payload.get("schema") != "rook.rag-eval-summary/v1":
        return "schema"
    if payload.get("dataset_path") != expected_dataset or payload.get("case_count") != 2:
        return "dataset_identity"
    ragas = payload.get("ragas", {})
    if ragas.get("status") != "not_observed":
        return "ragas_status"
    if ragas.get("evaluated_cases") != 0:
        return "ragas_case_count"
    if ragas.get("scores") not in ({}, None):
        return "invented_ragas_scores"
    if ragas.get("reason") != "No cases with non-empty ground_truth were found.":
        return "ragas_reason"
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    args = parser.parse_args()
    validators = {
        "compare-retrieval": validate_compare,
        "skipped-ragas": validate_skipped,
    }
    error = validators[args.case](Path.cwd())
    if error is not None:
        raise SystemExit(f"rag-evidence:{error}")


if __name__ == "__main__":
    main()
