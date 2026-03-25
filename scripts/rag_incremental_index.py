from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.candidate_store import load_batch
from src.db import init_db
from src.rag import DEFAULT_VECTOR_STORE_PATH, build_chunks_from_batch_candidate, index_documents, resolve_vector_store_path
from src.utils import load_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Incrementally index one batch or one candidate into the RAG vector store.")
    parser.add_argument("--batch-id", required=True, help="Candidate batch id to index.")
    parser.add_argument("--candidate-id", default="", help="Optional candidate id for single-candidate incremental indexing.")
    parser.add_argument("--collection", default="default", help="Vector store collection name.")
    args = parser.parse_args()

    load_env()
    init_db()

    payload = load_batch(str(args.batch_id or "").strip())
    if not payload:
        raise SystemExit(f"batch not found: {args.batch_id}")

    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    target_candidate_id = str(args.candidate_id or "").strip()
    if target_candidate_id:
        rows = [row for row in rows if str((row or {}).get("candidate_id") or "").strip() == target_candidate_id]
        if not rows:
            raise SystemExit(f"candidate not found in batch: {target_candidate_id}")

    documents: list[dict] = []
    for row in rows:
        candidate_id = str((row or {}).get("candidate_id") or "").strip()
        detail = details.get(candidate_id) if candidate_id else {}
        documents.extend(
            build_chunks_from_batch_candidate(
                jd_title=str(payload.get("jd_title") or ""),
                candidate_row=row if isinstance(row, dict) else {},
                detail_payload=detail if isinstance(detail, dict) else {},
                batch_id=str(payload.get("batch_id") or ""),
            )
        )

    if not documents:
        print("No candidate artifacts found for incremental indexing.")
        return 0

    store_path = resolve_vector_store_path(DEFAULT_VECTOR_STORE_PATH)
    summary = index_documents(
        documents,
        store_path=str(store_path),
        reset=False,
        collection=str(args.collection or "default"),
    )
    print("RAG incremental index finished")
    print(f"store_path: {store_path}")
    print(f"collection: {args.collection}")
    print(f"batch_id: {payload.get('batch_id')}")
    print(f"candidate_count: {len(rows)}")
    print(f"document_count: {len(documents)}")
    print(f"indexed_documents: {summary.get('indexed_documents')}")
    print(f"chunk_count: {summary.get('chunk_count')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
