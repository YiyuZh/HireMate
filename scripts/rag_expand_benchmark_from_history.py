from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.candidate_store import list_batches_by_jd, list_jd_titles, load_batch
from src.db import init_db
from src.rag import (
    build_cases_from_batch_candidate,
    build_cases_from_review_record,
    load_benchmark_cases,
    save_benchmark_cases,
)
from src.review_store import list_reviews
from src.utils import load_env


def _collect_review_cases(limit: int | None = None) -> tuple[list[dict], int]:
    reviews = list_reviews(limit=limit)
    cases: list[dict] = []
    for review in reviews:
        cases.extend(build_cases_from_review_record(review))
    return cases, len(reviews)


def _collect_batch_cases(limit_per_jd: int | None = None, candidate_limit: int | None = None) -> tuple[list[dict], int, int]:
    jd_titles = list_jd_titles()
    cases: list[dict] = []
    batch_count = 0
    candidate_count = 0

    for jd_title in jd_titles:
        batches = list_batches_by_jd(jd_title)
        if limit_per_jd is not None:
            batches = batches[: max(0, int(limit_per_jd))]
        for batch in batches:
            payload = load_batch(str(batch.get("batch_id") or ""))
            if not payload:
                continue
            batch_count += 1
            rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
            details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
            selected_rows = rows[: max(0, int(candidate_limit))] if candidate_limit is not None else rows
            for row in selected_rows:
                candidate_id = str((row or {}).get("candidate_id") or "").strip()
                detail = details.get(candidate_id) if candidate_id else {}
                cases.extend(
                    build_cases_from_batch_candidate(
                        jd_title=str(payload.get("jd_title") or jd_title),
                        candidate_row=row if isinstance(row, dict) else {},
                        detail_payload=detail if isinstance(detail, dict) else {},
                        batch_id=str(payload.get("batch_id") or ""),
                    )
                )
                candidate_count += 1
    return cases, batch_count, candidate_count


def _dedupe_cases(cases: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[str] = set()
    for case in cases:
        case_id = str(case.get("case_id") or "").strip() or str(case)
        if case_id in seen:
            continue
        seen.add(case_id)
        deduped.append(case)
    return deduped


def main() -> int:
    parser = argparse.ArgumentParser(description="Expand RAG benchmark cases from historical reviews and candidate batches.")
    parser.add_argument(
        "--output",
        default=str(ROOT_DIR / "data" / "rag_benchmark_historical.jsonl"),
        help="Output benchmark jsonl path.",
    )
    parser.add_argument(
        "--append-base-cases",
        action="store_true",
        help="Append existing sample benchmark cases into the output file.",
    )
    parser.add_argument(
        "--base-cases",
        default=str(ROOT_DIR / "data" / "rag_benchmark_samples.jsonl"),
        help="Base benchmark jsonl file used with --append-base-cases.",
    )
    parser.add_argument("--review-limit", type=int, default=None, help="Limit historical reviews.")
    parser.add_argument("--batch-limit-per-jd", type=int, default=None, help="Limit batch count per JD title.")
    parser.add_argument("--candidate-limit-per-batch", type=int, default=None, help="Limit candidate count per batch.")
    args = parser.parse_args()

    load_env()
    init_db()

    review_cases, review_count = _collect_review_cases(limit=args.review_limit)
    batch_cases, batch_count, candidate_count = _collect_batch_cases(
        limit_per_jd=args.batch_limit_per_jd,
        candidate_limit=args.candidate_limit_per_batch,
    )
    combined_cases = [*review_cases, *batch_cases]
    if args.append_base_cases:
        combined_cases = [*load_benchmark_cases(args.base_cases), *combined_cases]

    deduped_cases = _dedupe_cases(combined_cases)
    if not deduped_cases:
        print("No historical review or batch corpus found. No benchmark cases generated.")
        return 0

    save_benchmark_cases(args.output, deduped_cases)
    task_distribution = Counter(str(case.get("task") or "unknown") for case in deduped_cases)
    created_from_distribution = Counter(
        str(case.get("created_from") or "").split(":", 1)[0] or "unknown"
        for case in deduped_cases
    )

    print("RAG historical benchmark expanded")
    print(f"output: {Path(args.output).resolve()}")
    print(f"review_count: {review_count}")
    print(f"batch_count: {batch_count}")
    print(f"candidate_count: {candidate_count}")
    print(f"case_count: {len(deduped_cases)}")
    print(f"task_distribution: {dict(task_distribution)}")
    print(f"created_from_distribution: {dict(created_from_distribution)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
