from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.rag import (
    DEFAULT_VECTOR_STORE_PATH,
    load_benchmark_cases,
    resolve_rag_runtime_config,
    resolve_vector_store_path,
    run_benchmark,
)


def _load_all_cases(paths: list[str]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for raw_path in paths:
        for case in load_benchmark_cases(raw_path):
            case_id = str(case.get("case_id") or "").strip()
            dedupe_key = case_id or str(case)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged.append(case)
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RAG retrieval benchmark cases against the local vector store.")
    parser.add_argument(
        "--cases",
        nargs="+",
        default=[str(ROOT_DIR / "data" / "rag_benchmark_samples.jsonl")],
        help="One or more benchmark case jsonl files.",
    )
    parser.add_argument(
        "--store-path",
        default=str(resolve_vector_store_path(DEFAULT_VECTOR_STORE_PATH)),
        help="Vector store path.",
    )
    parser.add_argument(
        "--report-path",
        default="",
        help="Optional JSON report output path.",
    )
    args = parser.parse_args()

    store_path = str(resolve_vector_store_path(args.store_path))
    runtime_config = resolve_rag_runtime_config({"enabled": True})
    cases = _load_all_cases(args.cases)
    if not cases:
        raise SystemExit("benchmark case file is empty")

    summary = run_benchmark(cases, store_path=store_path, runtime_config=runtime_config)

    if args.report_path:
        report_path = Path(args.report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "summary": summary,
            "cases": cases,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("RAG benchmark")
    print(f"case_files: {len(args.cases)}")
    print(f"cases: {summary['cases']}")
    print(f"passed: {summary['passed']}")
    print(f"failed: {summary['failed']}")
    print(f"pass_rate: {summary['pass_rate']:.4f}")
    print(f"mean_combined_score: {summary['mean_combined_score']:.4f}")
    print(f"mean_grounding_recall: {summary['mean_grounding_recall']:.4f}")
    print(f"mean_counter_quality: {summary['mean_counter_quality']:.4f}")
    print(f"mean_explanation_consistency: {summary['mean_explanation_consistency']:.4f}")
    print(f"mean_source_rank: {summary['mean_source_rank']:.4f}")
    print(f"mean_substring_rank: {summary['mean_substring_rank']:.4f}")
    print(f"task_distribution: {summary['task_distribution']}")
    print()

    for task, metrics in (summary.get("task_metrics") or {}).items():
        print(
            f"[task={task}] count={metrics['count']} passed={metrics['passed']} "
            f"pass_rate={metrics['pass_rate']:.4f} mean_combined={metrics['mean_combined_score']:.4f}"
        )
    print()

    for item in summary.get("results", []):
        print(f"[{'PASS' if item['success'] else 'FAIL'}] {item['case_id']} | task={item['task']}")
        print(f"  query: {item['query']}")
        if item.get("created_from"):
            print(f"  created_from: {item['created_from']}")
        print(
            f"  ranks: source={float(item.get('source_rank') or 0.0):.4f} "
            f"substring={float(item.get('substring_rank') or 0.0):.4f} "
            f"label={float(item.get('label_rank') or 0.0):.4f}"
        )
        for hit in item.get("top_hits", []):
            print(
                f"  - [{hit['score']:.4f}] {hit['source_type']} | {hit['chunk_label']} | {hit['text']}"
            )
        print()

    return 0 if summary["passed"] == summary["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
