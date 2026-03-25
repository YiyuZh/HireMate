from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.rag import (
    DEFAULT_VECTOR_STORE_PATH,
    LocalVectorStore,
    metadata_has_required_fields,
    resolve_vector_store_path,
    retrieve_for_ai_reviewer,
    retrieve_for_evidence_grounding,
    retrieve_for_jd_alignment,
)


def _format_hit(item: dict) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    label = str(metadata.get("chunk_label") or metadata.get("source_type") or "chunk")
    score = float(item.get("score") or 0.0)
    preview = str(item.get("text") or "").replace("\n", " ").strip()
    if len(preview) > 80:
        preview = preview[:79].rstrip() + "…"
    return f"- [{score:.4f}] {label}: {preview}"


def main() -> None:
    store_path = resolve_vector_store_path(DEFAULT_VECTOR_STORE_PATH)
    store = LocalVectorStore(store_path)
    chunks = store.load_chunks()

    if not chunks:
        raise SystemExit("vector_store is empty. Run `uv run python scripts/rag_build_sample_index.py` first.")

    distribution = Counter(
        str((chunk.get("metadata") or {}).get("source_type") or "unknown")
        for chunk in chunks
    )
    metadata_ok = all(metadata_has_required_fields(chunk.get("metadata")) for chunk in chunks)

    alignment_query = "AI 产品经理实习生需要 SQL Python PRD A/B 测试与大模型相关经验"
    evidence_query = "请找出简历里与 SQL Python A/B 测试相关的关键证据"
    reviewer_query = "给 AI reviewer 补充与岗位模板匹配的方法 产出 结果 证据"

    print("RAG smoke test")
    print(f"store_path: {Path(store_path)}")
    print(f"chunk_count: {len(chunks)}")
    print(f"source_type_distribution: {dict(distribution)}")
    print(f"metadata_complete: {metadata_ok}")
    print()

    print("top_k for retrieve_for_jd_alignment:")
    for item in retrieve_for_jd_alignment(alignment_query, top_k=3, store_path=str(store_path)):
        print(_format_hit(item))
    print()

    print("top_k for retrieve_for_evidence_grounding:")
    for item in retrieve_for_evidence_grounding(evidence_query, top_k=3, store_path=str(store_path)):
        print(_format_hit(item))
    print()

    print("top_k for retrieve_for_ai_reviewer:")
    for item in retrieve_for_ai_reviewer(reviewer_query, top_k=3, store_path=str(store_path)):
        print(_format_hit(item))


if __name__ == "__main__":
    main()
