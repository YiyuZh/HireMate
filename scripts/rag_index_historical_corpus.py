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
    DEFAULT_VECTOR_STORE_PATH,
    build_chunks_from_batch_candidate,
    build_chunks_from_review_record,
    index_documents,
    resolve_vector_store_path,
)
from src.review_store import list_reviews
from src.utils import load_env


def _index_reviews(limit: int | None = None) -> tuple[list[dict], int]:
    reviews = list_reviews(limit=limit)
    documents: list[dict] = []
    for review in reviews:
        documents.extend(build_chunks_from_review_record(review))
    return documents, len(reviews)


def _index_batches(limit_per_jd: int | None = None, candidate_limit: int | None = None) -> tuple[list[dict], int, int]:
    jd_titles = list_jd_titles()
    documents: list[dict] = []
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
                documents.extend(
                    build_chunks_from_batch_candidate(
                        jd_title=str(payload.get("jd_title") or jd_title),
                        candidate_row=row if isinstance(row, dict) else {},
                        detail_payload=detail if isinstance(detail, dict) else {},
                        batch_id=str(payload.get("batch_id") or ""),
                    )
                )
                candidate_count += 1
    return documents, batch_count, candidate_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Build RAG index from historical reviews and candidate batches.")
    parser.add_argument("--reset", action="store_true", help="Reset the target collection before writing.")
    parser.add_argument("--review-limit", type=int, default=None, help="Limit historical reviews to index.")
    parser.add_argument("--batch-limit-per-jd", type=int, default=None, help="Limit batch count per JD title.")
    parser.add_argument("--candidate-limit-per-batch", type=int, default=None, help="Limit candidate count per batch.")
    parser.add_argument("--collection", default="default", help="Vector store collection name.")
    args = parser.parse_args()

    load_env()
    init_db()

    store_path = resolve_vector_store_path(DEFAULT_VECTOR_STORE_PATH)
    review_docs, review_count = _index_reviews(limit=args.review_limit)
    batch_docs, batch_count, candidate_count = _index_batches(
        limit_per_jd=args.batch_limit_per_jd,
        candidate_limit=args.candidate_limit_per_batch,
    )
    documents = [*review_docs, *batch_docs]

    if not documents:
        print("No historical review or batch corpus found. Nothing indexed.")
        return 0

    summary = index_documents(
        documents,
        store_path=str(store_path),
        reset=bool(args.reset),
        collection=str(args.collection or "default"),
    )
    distribution = Counter(
        str((doc.get("metadata") or {}).get("source_type") or "unknown")
        for doc in documents
    )

    print("RAG historical corpus index built")
    print(f"store_path: {store_path}")
    print(f"collection: {args.collection}")
    print(f"review_count: {review_count}")
    print(f"batch_count: {batch_count}")
    print(f"candidate_count: {candidate_count}")
    print(f"document_count: {len(documents)}")
    print(f"source_type_distribution: {dict(distribution)}")
    print(f"indexed_documents: {summary.get('indexed_documents')}")
    print(f"chunk_count: {summary.get('chunk_count')}")
    print(f"embedding_backend: {summary.get('embedding_backend')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
