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
            case_id = str(case.get("case_id") or "").strip() or str(case)
            if case_id in seen:
                continue
            seen.add(case_id)
            merged.append(case)
    return merged


def _candidate_weights() -> list[dict[str, float]]:
    candidates: list[dict[str, float]] = []
    semantic_values = [0.48, 0.56, 0.62, 0.68]
    lexical_values = [0.14, 0.2, 0.26]
    skill_values = [0.1, 0.16, 0.22]
    for semantic in semantic_values:
        for lexical in lexical_values:
            for skill in skill_values:
                source = round(1.0 - semantic - lexical - skill, 2)
                if source < 0.02 or source > 0.16:
                    continue
                candidates.append(
                    {
                        "enabled": True,
                        "semantic_weight": round(semantic, 2),
                        "lexical_weight": round(lexical, 2),
                        "skill_weight": round(skill, 2),
                        "source_type_weight": round(source, 2),
                        "dedupe_by_text": True,
                    }
                )
    current_default = {
        "enabled": True,
        "semantic_weight": 0.62,
        "lexical_weight": 0.22,
        "skill_weight": 0.12,
        "source_type_weight": 0.04,
        "dedupe_by_text": True,
    }
    if current_default not in candidates:
        candidates.append(current_default)
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="Sweep RAG rerank weights against benchmark cases.")
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
    parser.add_argument("--top-n", type=int, default=10, help="Show top N rerank configs.")
    parser.add_argument(
        "--report-out",
        default="",
        help="Optional json output path for the tuning report.",
    )
    args = parser.parse_args()

    store_path = str(resolve_vector_store_path(args.store_path))
    cases = _load_all_cases(args.cases)
    if not cases:
        raise SystemExit("no benchmark cases found")

    results: list[dict] = []
    for rerank_cfg in _candidate_weights():
        runtime_config = resolve_rag_runtime_config({"enabled": True, "rerank": rerank_cfg})
        summary = run_benchmark(cases, store_path=store_path, runtime_config=runtime_config)
        results.append(
            {
                "rerank": rerank_cfg,
                "cases": summary["cases"],
                "passed": summary["passed"],
                "failed": summary["failed"],
                "pass_rate": summary["pass_rate"],
                "mean_combined_score": summary["mean_combined_score"],
                "mean_source_rank": summary["mean_source_rank"],
                "mean_substring_rank": summary["mean_substring_rank"],
                "task_metrics": summary.get("task_metrics") or {},
            }
        )

    ranked = sorted(
        results,
        key=lambda item: (
            float(item.get("pass_rate") or 0.0),
            float(item.get("mean_combined_score") or 0.0),
            float(item.get("mean_source_rank") or 0.0),
            float(item.get("mean_substring_rank") or 0.0),
        ),
        reverse=True,
    )
    best = ranked[0]

    print("RAG rerank tuning")
    print(f"cases: {len(cases)}")
    print(f"candidate_configs: {len(ranked)}")
    print("recommended_rerank:")
    print(json.dumps(best["rerank"], ensure_ascii=False, indent=2))
    print(
        f"recommended_score: pass_rate={best['pass_rate']:.4f} "
        f"mean_combined={best['mean_combined_score']:.4f} "
        f"mean_source_rank={best['mean_source_rank']:.4f} "
        f"mean_substring_rank={best['mean_substring_rank']:.4f}"
    )
    print()

    for index, item in enumerate(ranked[: max(1, int(args.top_n))], start=1):
        rerank_cfg = item["rerank"]
        print(
            f"[{index}] pass_rate={item['pass_rate']:.4f} "
            f"mean_combined={item['mean_combined_score']:.4f} "
            f"weights={rerank_cfg}"
        )

    if args.report_out:
        report_path = Path(args.report_out).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(ranked, ensure_ascii=False, indent=2), encoding="utf-8")
        print()
        print(f"report_out: {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
