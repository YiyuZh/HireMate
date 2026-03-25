from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


DEFAULT_VECTOR_STORE_PATH = Path("/app/data/vector_store")
DEFAULT_COLLECTION = "default"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_vector_store_path(path: str | Path | None = None) -> Path:
    target = Path(path) if path else DEFAULT_VECTOR_STORE_PATH
    target.mkdir(parents=True, exist_ok=True)
    return target


class LocalVectorStore:
    def __init__(self, base_path: str | Path | None = None, *, collection: str = DEFAULT_COLLECTION) -> None:
        self.base_path = resolve_vector_store_path(base_path)
        self.collection = str(collection or DEFAULT_COLLECTION).strip() or DEFAULT_COLLECTION

    @property
    def manifest_path(self) -> Path:
        return self.base_path / "manifest.json"

    @property
    def collections_dir(self) -> Path:
        return self.base_path / "collections"

    @property
    def collection_dir(self) -> Path:
        return self.collections_dir / self.collection

    @property
    def chunks_path(self) -> Path:
        return self.collection_dir / "chunks.jsonl"

    @property
    def embeddings_path(self) -> Path:
        return self.collection_dir / "embeddings.jsonl"

    @property
    def stats_path(self) -> Path:
        return self.collection_dir / "stats.json"

    def ensure_layout(self) -> None:
        self.collection_dir.mkdir(parents=True, exist_ok=True)

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
        return rows

    def _write_jsonl(self, path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
        path.write_text(content + ("\n" if content else ""), encoding="utf-8")

    def load_chunks(self) -> list[dict[str, Any]]:
        return self._read_jsonl(self.chunks_path)

    def load_embeddings(self) -> dict[str, list[float]]:
        rows = self._read_jsonl(self.embeddings_path)
        return {
            str(row.get("chunk_id") or "").strip(): [float(value) for value in (row.get("embedding") or [])]
            for row in rows
            if str(row.get("chunk_id") or "").strip()
        }

    def load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {}
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def load_stats(self) -> dict[str, Any]:
        if not self.stats_path.exists():
            return {}
        return json.loads(self.stats_path.read_text(encoding="utf-8"))

    def describe(self) -> dict[str, Any]:
        stats = self.load_stats()
        if stats:
            return stats
        chunks = self.load_chunks()
        distribution = Counter(
            str((chunk.get("metadata") or {}).get("source_type") or "unknown")
            for chunk in chunks
        )
        return {
            "collection": self.collection,
            "store_path": str(self.base_path),
            "chunk_count": len(chunks),
            "source_type_distribution": dict(distribution),
        }

    def save_documents(
        self,
        documents: list[dict[str, Any]],
        embeddings: dict[str, list[float]],
        *,
        embedding_backend: str,
        embedding_dim: int,
        embedding_config: dict[str, Any] | None = None,
        reset: bool = False,
    ) -> dict[str, Any]:
        self.ensure_layout()

        existing_docs = {} if reset else {str(doc.get("chunk_id") or ""): doc for doc in self.load_chunks()}
        existing_embeddings = {} if reset else self.load_embeddings()

        for document in documents:
            chunk_id = str(document.get("chunk_id") or "").strip()
            if not chunk_id:
                continue
            existing_docs[chunk_id] = document
            if chunk_id in embeddings:
                existing_embeddings[chunk_id] = embeddings[chunk_id]

        ordered_docs = sorted(existing_docs.values(), key=lambda item: str(item.get("chunk_id") or ""))
        ordered_embeddings = [
            {"chunk_id": chunk_id, "embedding": existing_embeddings[chunk_id]}
            for chunk_id in sorted(existing_embeddings)
            if chunk_id in existing_docs
        ]

        distribution = Counter(
            str((doc.get("metadata") or {}).get("source_type") or "unknown")
            for doc in ordered_docs
        )

        self._write_jsonl(self.chunks_path, ordered_docs)
        self._write_jsonl(self.embeddings_path, ordered_embeddings)

        stats = {
            "collection": self.collection,
            "store_path": str(self.base_path),
            "chunk_count": len(ordered_docs),
            "embedding_count": len(ordered_embeddings),
            "embedding_backend": str(embedding_backend or "unknown"),
            "embedding_dim": int(embedding_dim or 0),
            "embedding_config": dict(embedding_config or {}),
            "source_type_distribution": dict(distribution),
            "updated_at": _utc_now_iso(),
        }
        self.stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

        manifest = self.load_manifest()
        manifest.update(
            {
                "version": 1,
                "default_collection": self.collection,
                "collections": sorted(set([*(manifest.get("collections") or []), self.collection])),
                "updated_at": _utc_now_iso(),
                "embedding_backend": str(embedding_backend or "unknown"),
                "embedding_dim": int(embedding_dim or 0),
                "embedding_config": dict(embedding_config or {}),
                "store_path": str(self.base_path),
            }
        )
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return stats
