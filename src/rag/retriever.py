from __future__ import annotations

import math
import os
import re
from typing import Any

from .corpus import index_runtime_context
from .indexer import build_embedding_provider, resolve_embedding_runtime_config
from .metadata import extract_skill_tags_from_text, resolve_role_family, safe_identifier
from .store import LocalVectorStore, resolve_vector_store_path


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+/.#-]*|[\u4e00-\u9fff]{2,}")
_ALIGNMENT_SYNONYM_GROUPS = [
    {"llm", "大模型", "大语言模型"},
    {"prompt", "提示词", "prompt engineering", "提示词工程"},
    {"rag", "检索增强", "检索增强生成"},
    {"agent", "智能体", "ai agent"},
    {"prd", "需求文档", "产品需求文档"},
    {"a/b测试", "ab测试", "a/b 测试"},
    {"用户研究", "用户访谈", "问卷", "可用性测试"},
    {"数据分析", "指标分析", "指标体系"},
]
_METHOD_HINTS = {
    "需求分析",
    "prd",
    "原型",
    "sql",
    "python",
    "用户访谈",
    "问卷",
    "可用性测试",
    "指标",
    "a/b",
    "实验",
    "研究",
    "prompt",
    "rag",
    "agent",
}
_RESULT_HINTS = {
    "提升",
    "降低",
    "增长",
    "优化",
    "转化",
    "效率",
    "结论",
    "洞察",
    "复盘",
    "上线",
}
DEFAULT_RAG_RUNTIME_CONFIG = {
    "enabled": False,
    "vector_store_path": "",
    "collection": "default",
    "features": {
        "jd_alignment": False,
        "evidence_grounding": False,
        "ai_reviewer_grounding": False,
        "full_grounding": False,
        "semantic_anchors": False,
        "counter_evidence": False,
        "missing_evidence": False,
        "historical_grounding": False,
        "risk_grounding": False,
    },
    "top_k": {
        "jd_alignment": 4,
        "evidence_grounding": 4,
        "ai_reviewer_grounding": 4,
        "semantic_anchors": 4,
        "counter_evidence": 4,
        "missing_evidence": 4,
        "historical_grounding": 4,
        "risk_grounding": 4,
    },
    "auto_index": {
        "runtime_context": True,
    },
    "rerank": {
        "enabled": True,
        "semantic_weight": 0.48,
        "lexical_weight": 0.14,
        "skill_weight": 0.22,
        "source_type_weight": 0.16,
        "dedupe_by_text": True,
    },
    "embedding": resolve_embedding_runtime_config({"provider": "mock"}),
}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def resolve_rag_runtime_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(config or {})
    features_cfg = payload.get("features") if isinstance(payload.get("features"), dict) else {}
    top_k_cfg = payload.get("top_k") if isinstance(payload.get("top_k"), dict) else {}

    enabled = bool(payload.get("enabled")) if "enabled" in payload else _env_flag("HIREMATE_RAG_ENABLE", False)
    vector_store_path = str(
        payload.get("vector_store_path")
        or os.getenv("HIREMATE_RAG_VECTOR_STORE_PATH")
        or DEFAULT_RAG_RUNTIME_CONFIG["vector_store_path"]
    ).strip()
    collection = str(
        payload.get("collection")
        or os.getenv("HIREMATE_RAG_COLLECTION")
        or DEFAULT_RAG_RUNTIME_CONFIG["collection"]
    ).strip() or "default"

    features = {
        "jd_alignment": bool(features_cfg.get("jd_alignment"))
        if "jd_alignment" in features_cfg
        else _env_flag("HIREMATE_RAG_ENABLE_JD_ALIGNMENT", enabled),
        "evidence_grounding": bool(features_cfg.get("evidence_grounding"))
        if "evidence_grounding" in features_cfg
        else _env_flag("HIREMATE_RAG_ENABLE_EVIDENCE_GROUNDING", enabled),
        "ai_reviewer_grounding": bool(features_cfg.get("ai_reviewer_grounding"))
        if "ai_reviewer_grounding" in features_cfg
        else _env_flag("HIREMATE_RAG_ENABLE_AI_REVIEWER_GROUNDING", enabled),
        "full_grounding": bool(features_cfg.get("full_grounding"))
        if "full_grounding" in features_cfg
        else _env_flag("HIREMATE_RAG_ENABLE_FULL_GROUNDING", enabled),
        "semantic_anchors": bool(features_cfg.get("semantic_anchors"))
        if "semantic_anchors" in features_cfg
        else _env_flag("HIREMATE_RAG_ENABLE_SEMANTIC_ANCHORS", enabled),
        "counter_evidence": bool(features_cfg.get("counter_evidence"))
        if "counter_evidence" in features_cfg
        else _env_flag("HIREMATE_RAG_ENABLE_COUNTER_EVIDENCE", enabled),
        "missing_evidence": bool(features_cfg.get("missing_evidence"))
        if "missing_evidence" in features_cfg
        else _env_flag("HIREMATE_RAG_ENABLE_MISSING_EVIDENCE", enabled),
        "historical_grounding": bool(features_cfg.get("historical_grounding"))
        if "historical_grounding" in features_cfg
        else _env_flag("HIREMATE_RAG_ENABLE_HISTORICAL_GROUNDING", enabled),
        "risk_grounding": bool(features_cfg.get("risk_grounding"))
        if "risk_grounding" in features_cfg
        else _env_flag("HIREMATE_RAG_ENABLE_RISK_GROUNDING", enabled),
    }

    top_k = {}
    for key, default_value in DEFAULT_RAG_RUNTIME_CONFIG["top_k"].items():
        value = top_k_cfg.get(key, os.getenv(f"HIREMATE_RAG_TOP_K_{key.upper()}", default_value))
        try:
            top_k[key] = max(1, int(value or default_value))
        except (TypeError, ValueError):
            top_k[key] = default_value

    auto_index_cfg = payload.get("auto_index") if isinstance(payload.get("auto_index"), dict) else {}
    auto_index = {
        "runtime_context": bool(auto_index_cfg.get("runtime_context"))
        if "runtime_context" in auto_index_cfg
        else _env_flag("HIREMATE_RAG_AUTO_INDEX_RUNTIME_CONTEXT", True),
    }

    rerank_cfg = payload.get("rerank") if isinstance(payload.get("rerank"), dict) else {}
    rerank = {
        "enabled": bool(rerank_cfg.get("enabled")) if "enabled" in rerank_cfg else _env_flag("HIREMATE_RAG_RERANK_ENABLE", True),
        "semantic_weight": float(rerank_cfg.get("semantic_weight"))
        if "semantic_weight" in rerank_cfg
        else _env_float(
            "HIREMATE_RAG_RERANK_SEMANTIC_WEIGHT",
            DEFAULT_RAG_RUNTIME_CONFIG["rerank"]["semantic_weight"],
        ),
        "lexical_weight": float(rerank_cfg.get("lexical_weight"))
        if "lexical_weight" in rerank_cfg
        else _env_float(
            "HIREMATE_RAG_RERANK_LEXICAL_WEIGHT",
            DEFAULT_RAG_RUNTIME_CONFIG["rerank"]["lexical_weight"],
        ),
        "skill_weight": float(rerank_cfg.get("skill_weight"))
        if "skill_weight" in rerank_cfg
        else _env_float(
            "HIREMATE_RAG_RERANK_SKILL_WEIGHT",
            DEFAULT_RAG_RUNTIME_CONFIG["rerank"]["skill_weight"],
        ),
        "source_type_weight": float(rerank_cfg.get("source_type_weight"))
        if "source_type_weight" in rerank_cfg
        else _env_float(
            "HIREMATE_RAG_RERANK_SOURCE_TYPE_WEIGHT",
            DEFAULT_RAG_RUNTIME_CONFIG["rerank"]["source_type_weight"],
        ),
        "dedupe_by_text": bool(rerank_cfg.get("dedupe_by_text"))
        if "dedupe_by_text" in rerank_cfg
        else _env_flag("HIREMATE_RAG_RERANK_DEDUPE_BY_TEXT", True),
    }

    return {
        "enabled": enabled,
        "vector_store_path": vector_store_path,
        "collection": collection,
        "features": features,
        "top_k": top_k,
        "auto_index": auto_index,
        "rerank": rerank,
        "embedding": resolve_embedding_runtime_config(
            payload.get("embedding") if isinstance(payload.get("embedding"), dict) else None
        ),
    }


def rag_feature_enabled(feature: str, runtime_config: dict[str, Any] | None = None) -> bool:
    config = resolve_rag_runtime_config(runtime_config)
    return bool(config.get("enabled")) and bool((config.get("features") or {}).get(feature))


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _matches_filters(
    metadata: dict[str, Any],
    *,
    source_types: set[str] | None = None,
    role_family: str = "",
    job_id_safe: str = "",
    candidate_id_safe: str = "",
) -> bool:
    if source_types and str(metadata.get("source_type") or "") not in source_types:
        return False
    if role_family and str(metadata.get("role_family") or "").strip() != str(role_family).strip():
        return False
    if job_id_safe and str(metadata.get("job_id_safe") or "").strip() != str(job_id_safe).strip():
        return False
    if candidate_id_safe and str(metadata.get("candidate_id_safe") or "").strip() != str(candidate_id_safe).strip():
        return False
    return True


def _store_embedding_config(store: LocalVectorStore) -> dict[str, Any]:
    stats = store.load_stats()
    config = stats.get("embedding_config")
    return dict(config) if isinstance(config, dict) else {}


def _normalized_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").lower())


def _token_overlap_ratio(left: list[str], right: list[str]) -> float:
    left_set = {item.lower() for item in left if str(item).strip()}
    right_set = {item.lower() for item in right if str(item).strip()}
    if not left_set or not right_set:
        return 0.0
    overlap = left_set & right_set
    return len(overlap) / max(1, len(left_set))


def _default_source_priorities(source_types: set[str] | None) -> dict[str, float]:
    if not source_types:
        return {}
    if source_types == {"jd", "role_profile", "rubric"}:
        return {"jd": 1.0, "role_profile": 0.85, "rubric": 0.8}
    if source_types == {"resume_fragment", "evidence", "rubric"}:
        return {"evidence": 1.0, "resume_fragment": 0.9, "rubric": 0.7}
    return {"evidence": 0.95, "resume_fragment": 0.9, "role_profile": 0.82, "rubric": 0.78, "jd": 0.75}


def _rerank_results(
    query: str,
    results: list[dict[str, Any]],
    *,
    source_types: set[str] | None = None,
    rerank_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not results:
        return []

    config = dict(rerank_config or {})
    if not bool(config.get("enabled", True)):
        return results

    query_tokens = _tokenize_text(query)
    query_skill_terms = extract_skill_tags_from_text(query, extra_candidates=query_tokens)
    source_priorities = _default_source_priorities(source_types)

    reranked: list[dict[str, Any]] = []
    for item in results:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        text = str(item.get("text") or "")
        text_tokens = _tokenize_text(text)
        lexical = _token_overlap_ratio(query_tokens, text_tokens)
        skill_tags = metadata.get("skill_tags") if isinstance(metadata.get("skill_tags"), list) else []
        skill_overlap = _token_overlap_ratio(query_skill_terms or query_tokens, [*skill_tags, *text_tokens])
        source_type = str(metadata.get("source_type") or "")
        source_bonus = float(source_priorities.get(source_type, 0.5))
        semantic = float(item.get("score") or 0.0)
        fused = (
            semantic * float(config.get("semantic_weight", 0.62))
            + lexical * float(config.get("lexical_weight", 0.22))
            + skill_overlap * float(config.get("skill_weight", 0.12))
            + source_bonus * float(config.get("source_type_weight", 0.04))
        )
        reranked.append(
            {
                **item,
                "semantic_score": round(semantic, 6),
                "lexical_score": round(lexical, 6),
                "skill_overlap_score": round(skill_overlap, 6),
                "fused_score": round(fused, 6),
            }
        )

    reranked.sort(key=lambda item: (float(item.get("fused_score") or 0.0), len(str(item.get("text") or ""))), reverse=True)

    if not bool(config.get("dedupe_by_text", True)):
        return reranked

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in reranked:
        key = _normalized_key(str(item.get("text") or ""))
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _search(
    query: str,
    *,
    top_k: int = 5,
    source_types: set[str] | None = None,
    role_family: str = "",
    job_id_safe: str = "",
    candidate_id_safe: str = "",
    store_path: str | None = None,
    collection: str = "default",
    runtime_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    config = resolve_rag_runtime_config(runtime_config)
    effective_store_path = store_path or config.get("vector_store_path") or None
    effective_collection = str(collection or config.get("collection") or "default").strip() or "default"
    store = LocalVectorStore(effective_store_path, collection=effective_collection)
    chunks = store.load_chunks()
    if not chunks:
        return []

    embedding_config = {**_store_embedding_config(store), **(config.get("embedding") or {})}
    provider = build_embedding_provider(embedding_config)
    embeddings = store.load_embeddings()
    query_embedding = provider.embed_text(query)

    results: list[dict[str, Any]] = []
    for chunk in chunks:
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        if not _matches_filters(
            metadata,
            source_types=source_types,
            role_family=role_family,
            job_id_safe=job_id_safe,
            candidate_id_safe=candidate_id_safe,
        ):
            continue

        chunk_id = str(chunk.get("chunk_id") or "").strip()
        if not chunk_id or chunk_id not in embeddings:
            continue

        results.append(
            {
                "chunk_id": chunk_id,
                "document_id": str(chunk.get("document_id") or "").strip(),
                "score": round(float(_cosine_similarity(query_embedding, embeddings[chunk_id])), 6),
                "text": str(chunk.get("text") or "").strip(),
                "metadata": metadata,
            }
        )

    reranked = _rerank_results(query, results, source_types=source_types, rerank_config=config.get("rerank"))
    return reranked[: max(1, int(top_k or 5))]


def retrieve_for_jd_alignment(
    query: str,
    *,
    top_k: int = 5,
    role_family: str = "",
    job_id_safe: str = "",
    store_path: str | None = None,
    collection: str = "default",
    runtime_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return _search(
        query,
        top_k=top_k,
        source_types={"jd", "role_profile", "rubric"},
        role_family=role_family,
        job_id_safe=job_id_safe,
        store_path=store_path,
        collection=collection,
        runtime_config=runtime_config,
    )


def retrieve_for_evidence_grounding(
    query: str,
    *,
    top_k: int = 5,
    role_family: str = "",
    job_id_safe: str = "",
    candidate_id_safe: str = "",
    store_path: str | None = None,
    collection: str = "default",
    runtime_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return _search(
        query,
        top_k=top_k,
        source_types={"resume_fragment", "evidence", "rubric"},
        role_family=role_family,
        job_id_safe=job_id_safe,
        candidate_id_safe=candidate_id_safe,
        store_path=store_path,
        collection=collection,
        runtime_config=runtime_config,
    )


def retrieve_for_ai_reviewer(
    query: str,
    *,
    top_k: int = 5,
    role_family: str = "",
    job_id_safe: str = "",
    candidate_id_safe: str = "",
    store_path: str | None = None,
    collection: str = "default",
    runtime_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return _search(
        query,
        top_k=top_k,
        source_types={"jd", "role_profile", "resume_fragment", "evidence", "rubric"},
        role_family=role_family,
        job_id_safe=job_id_safe,
        candidate_id_safe=candidate_id_safe,
        store_path=store_path,
        collection=collection,
        runtime_config=runtime_config,
    )


def retrieve_for_semantic_anchors(
    query: str,
    *,
    top_k: int = 5,
    role_family: str = "",
    job_id_safe: str = "",
    store_path: str | None = None,
    collection: str = "default",
    runtime_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return _search(
        query,
        top_k=top_k,
        source_types={"jd", "role_profile", "rubric"},
        role_family=role_family,
        job_id_safe=job_id_safe,
        store_path=store_path,
        collection=collection,
        runtime_config=runtime_config,
    )


def retrieve_for_counter_evidence(
    query: str,
    *,
    top_k: int = 5,
    role_family: str = "",
    job_id_safe: str = "",
    candidate_id_safe: str = "",
    store_path: str | None = None,
    collection: str = "default",
    runtime_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return _search(
        query,
        top_k=top_k,
        source_types={"resume_fragment", "evidence", "rubric"},
        role_family=role_family,
        job_id_safe=job_id_safe,
        candidate_id_safe=candidate_id_safe,
        store_path=store_path,
        collection=collection,
        runtime_config=runtime_config,
    )


def retrieve_for_missing_evidence(
    query: str,
    *,
    top_k: int = 5,
    role_family: str = "",
    job_id_safe: str = "",
    store_path: str | None = None,
    collection: str = "default",
    runtime_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return _search(
        query,
        top_k=top_k,
        source_types={"jd", "role_profile", "rubric"},
        role_family=role_family,
        job_id_safe=job_id_safe,
        store_path=store_path,
        collection=collection,
        runtime_config=runtime_config,
    )


def retrieve_for_historical_grounding(
    query: str,
    *,
    top_k: int = 5,
    role_family: str = "",
    job_id_safe: str = "",
    store_path: str | None = None,
    collection: str = "default",
    runtime_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return _search(
        query,
        top_k=top_k,
        source_types={"evidence", "resume_fragment", "rubric"},
        role_family=role_family,
        job_id_safe=job_id_safe,
        store_path=store_path,
        collection=collection,
        runtime_config=runtime_config,
    )


def retrieve_for_risk_grounding(
    query: str,
    *,
    top_k: int = 5,
    role_family: str = "",
    job_id_safe: str = "",
    candidate_id_safe: str = "",
    store_path: str | None = None,
    collection: str = "default",
    runtime_config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return _search(
        query,
        top_k=top_k,
        source_types={"evidence", "rubric"},
        role_family=role_family,
        job_id_safe=job_id_safe,
        candidate_id_safe=candidate_id_safe,
        store_path=store_path,
        collection=collection,
        runtime_config=runtime_config,
    )


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(clean)
    return deduped


def _tokenize_text(text: str) -> list[str]:
    return _dedupe(_TOKEN_RE.findall(str(text or "")))


def _summarize_hits(results: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for item in results[:limit]:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        text = str(item.get("text") or "").strip()
        preview = text if len(text) <= 120 else text[:119].rstrip() + "…"
        summary.append(
            {
                "score": float(item.get("fused_score") or item.get("score") or 0.0),
                "chunk_label": str(metadata.get("chunk_label") or metadata.get("source_type") or "chunk"),
                "source_type": str(metadata.get("source_type") or ""),
                "skill_tags": list(metadata.get("skill_tags") or []) if isinstance(metadata.get("skill_tags"), list) else [],
                "text": preview,
            }
        )
    return summary


def _collect_skill_terms(results: list[dict[str, Any]]) -> list[str]:
    collected: list[str] = []
    for item in results:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        skill_tags = metadata.get("skill_tags") if isinstance(metadata.get("skill_tags"), list) else []
        collected.extend(str(tag) for tag in skill_tags if str(tag).strip())
        collected.extend(extract_skill_tags_from_text(str(item.get("text") or ""), extra_candidates=skill_tags))
    return _dedupe(collected)


def _collect_terms_by_hint(results: list[dict[str, Any]], hint_terms: set[str]) -> list[str]:
    hits: list[str] = []
    for item in results:
        tokens = _tokenize_text(str(item.get("text") or ""))
        for token in tokens:
            normalized = token.lower()
            if any(hint in normalized or normalized in hint for hint in hint_terms):
                hits.append(token)
    return _dedupe(hits)


def _build_query(parts: list[str]) -> str:
    return " ".join(str(part or "").strip() for part in parts if str(part or "").strip()).strip()


def _cluster_aliases(base_skill: str, candidates: list[str]) -> list[str]:
    base_norm = str(base_skill or "").strip().lower()
    aliases: list[str] = []
    for group in _ALIGNMENT_SYNONYM_GROUPS:
        if base_norm not in {item.lower() for item in group}:
            continue
        for candidate in candidates:
            candidate_clean = str(candidate or "").strip()
            if not candidate_clean:
                continue
            if candidate_clean.lower() in {item.lower() for item in group} and candidate_clean.lower() != base_norm:
                aliases.append(candidate_clean)
        break
    return _dedupe(aliases)


def expand_jd_with_rag(
    parsed_jd: dict[str, Any],
    *,
    jd_text: str = "",
    runtime_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(parsed_jd or {})
    config = resolve_rag_runtime_config(runtime_config)
    if not rag_feature_enabled("jd_alignment", config):
        payload["rag_alignment"] = {
            "enabled": False,
            "reason": "jd_alignment disabled",
            "retrieved_count": 0,
            "retrieved_chunks": [],
            "related_skill_aliases": [],
        }
        return payload

    role_family = resolve_role_family(payload)
    job_id_safe = safe_identifier(str(payload.get("job_title") or jd_text[:32] or ""), "job")
    query = _build_query(
        [
            payload.get("job_title"),
            " ".join(payload.get("required_skills") or []),
            " ".join(payload.get("bonus_skills") or []),
            jd_text[:400],
        ]
    )
    try:
        results = retrieve_for_jd_alignment(
            query,
            top_k=(config.get("top_k") or {}).get("jd_alignment", 4),
            role_family=role_family,
            job_id_safe=job_id_safe,
            runtime_config=config,
        )
    except Exception as exc:  # noqa: BLE001
        payload["rag_alignment"] = {
            "enabled": False,
            "reason": f"jd_alignment failed: {exc}",
            "retrieved_count": 0,
            "retrieved_chunks": [],
            "related_skill_aliases": [],
        }
        return payload

    related_skill_terms = _collect_skill_terms(results)
    required_aliases_map = {
        str(skill): _cluster_aliases(str(skill), related_skill_terms)
        for skill in (payload.get("required_skills") or [])
    }
    bonus_aliases_map = {
        str(skill): _cluster_aliases(str(skill), related_skill_terms)
        for skill in (payload.get("bonus_skills") or [])
    }
    expanded_required = _dedupe(
        [*(payload.get("required_skills") or []), *[alias for values in required_aliases_map.values() for alias in values]]
    )
    expanded_bonus = _dedupe(
        [*(payload.get("bonus_skills") or []), *[alias for values in bonus_aliases_map.values() for alias in values]]
    )

    payload["required_skill_aliases_map"] = required_aliases_map
    payload["bonus_skill_aliases_map"] = bonus_aliases_map
    payload["expanded_required_skills"] = expanded_required
    payload["expanded_bonus_skills"] = expanded_bonus
    payload["rag_alignment"] = {
        "enabled": True,
        "reason": "vector_store retrieval applied",
        "retrieved_count": len(results),
        "retrieved_chunks": _summarize_hits(results),
        "related_skill_aliases": _dedupe([*expanded_required, *expanded_bonus]),
        "query": query,
    }
    return payload


def build_evidence_grounding(
    parsed_resume: dict[str, Any],
    *,
    parsed_jd: dict[str, Any] | None = None,
    role_profile: dict[str, Any] | None = None,
    runtime_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = resolve_rag_runtime_config(runtime_config)
    if not rag_feature_enabled("evidence_grounding", config):
        return {
            "enabled": False,
            "reason": "evidence_grounding disabled",
            "retrieved_count": 0,
            "results": [],
            "jd_terms": [],
            "method_terms": [],
            "result_terms": [],
        }

    jd_payload = parsed_jd if isinstance(parsed_jd, dict) else {}
    role_family = resolve_role_family(jd_payload, fallback_profile_name=str((role_profile or {}).get("profile_name") or ""))
    job_id_safe = safe_identifier(str(jd_payload.get("job_title") or ""), "job")
    candidate_id_safe = safe_identifier(str(parsed_resume.get("name") or ""), "cand")
    if bool((config.get("auto_index") or {}).get("runtime_context")):
        try:
            index_runtime_context(
                parsed_jd=jd_payload,
                parsed_resume=parsed_resume,
                resume_text=str(parsed_resume.get("normalized_resume_text") or parsed_resume.get("raw_resume_text") or ""),
                candidate_id=str(parsed_resume.get("name") or ""),
                job_id=str(jd_payload.get("job_title") or ""),
                store_path=config.get("vector_store_path") or None,
                collection=str(config.get("collection") or "default"),
                embedding_config=config.get("embedding"),
            )
        except Exception:
            pass
    query = _build_query(
        [
            jd_payload.get("job_title"),
            " ".join(jd_payload.get("expanded_required_skills") or jd_payload.get("required_skills") or []),
            " ".join(parsed_resume.get("skills") or []),
            parsed_resume.get("education"),
        ]
    )
    try:
        results = retrieve_for_evidence_grounding(
            query,
            top_k=(config.get("top_k") or {}).get("evidence_grounding", 4),
            role_family=role_family,
            job_id_safe=job_id_safe,
            candidate_id_safe=candidate_id_safe,
            runtime_config=config,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "enabled": False,
            "reason": f"evidence_grounding failed: {exc}",
            "retrieved_count": 0,
            "results": [],
            "jd_terms": [],
            "method_terms": [],
            "result_terms": [],
        }

    return {
        "enabled": True,
        "reason": "vector_store retrieval applied",
        "retrieved_count": len(results),
        "results": _summarize_hits(results),
        "jd_terms": _collect_skill_terms(results),
        "method_terms": _collect_terms_by_hint(results, _METHOD_HINTS),
        "result_terms": _collect_terms_by_hint(results, _RESULT_HINTS),
        "query": query,
    }


def build_ai_reviewer_grounding(
    parsed_jd: dict[str, Any],
    parsed_resume: dict[str, Any],
    *,
    score_details: dict[str, Any] | None = None,
    evidence_snippets: list[dict[str, Any]] | None = None,
    screening_result: dict[str, Any] | None = None,
    runtime_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = resolve_rag_runtime_config(runtime_config)
    if not rag_feature_enabled("ai_reviewer_grounding", config):
        return {
            "enabled": False,
            "reason": "ai_reviewer_grounding disabled",
            "retrieved_count": 0,
            "results": [],
        }

    role_family = resolve_role_family(parsed_jd)
    job_id_safe = safe_identifier(str(parsed_jd.get("job_title") or ""), "job")
    candidate_id_safe = safe_identifier(str(parsed_resume.get("name") or ""), "cand")
    if bool((config.get("auto_index") or {}).get("runtime_context")):
        try:
            index_runtime_context(
                parsed_jd=parsed_jd,
                parsed_resume=parsed_resume,
                resume_text=str(parsed_resume.get("normalized_resume_text") or parsed_resume.get("raw_resume_text") or ""),
                score_details=score_details if isinstance(score_details, dict) else {},
                evidence_snippets=evidence_snippets,
                screening_reasons=(screening_result or {}).get("screening_reasons") if isinstance((screening_result or {}).get("screening_reasons"), list) else [],
                candidate_id=str(parsed_resume.get("name") or ""),
                job_id=str(parsed_jd.get("job_title") or ""),
                store_path=config.get("vector_store_path") or None,
                collection=str(config.get("collection") or "default"),
                embedding_config=config.get("embedding"),
            )
        except Exception:
            pass
    snippet_text = " ".join(str((item or {}).get("text") or "") for item in (evidence_snippets or [])[:3] if isinstance(item, dict))
    reason_text = " ".join(str(item or "") for item in ((screening_result or {}).get("screening_reasons") or [])[:3])
    query = _build_query(
        [
            parsed_jd.get("job_title"),
            " ".join(parsed_jd.get("expanded_required_skills") or parsed_jd.get("required_skills") or []),
            " ".join(parsed_resume.get("skills") or []),
            snippet_text,
            reason_text,
        ]
    )
    try:
        results = retrieve_for_ai_reviewer(
            query,
            top_k=(config.get("top_k") or {}).get("ai_reviewer_grounding", 4),
            role_family=role_family,
            job_id_safe=job_id_safe,
            candidate_id_safe=candidate_id_safe,
            runtime_config=config,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "enabled": False,
            "reason": f"ai_reviewer_grounding failed: {exc}",
            "retrieved_count": 0,
            "results": [],
        }

    return {
        "enabled": True,
        "reason": "vector_store retrieval applied",
        "retrieved_count": len(results),
        "results": _summarize_hits(results),
        "query": query,
    }


def build_full_grounding(
    *,
    parsed_jd: dict[str, Any],
    parsed_resume: dict[str, Any],
    evidence_snippets: list[dict[str, Any]] | None = None,
    screening_reasons: list[str] | None = None,
    runtime_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = resolve_rag_runtime_config(runtime_config)
    if not rag_feature_enabled("full_grounding", config):
        return {
            "enabled": False,
            "reason": "full_grounding disabled",
            "jd_semantic_anchors": [],
            "positive_evidence": [],
            "counter_evidence": [],
            "missing_evidence": [],
            "historical_case_grounding": [],
            "risk_case_grounding": [],
        }

    role_family = resolve_role_family(parsed_jd)
    job_id_safe = safe_identifier(str(parsed_jd.get("job_title") or ""), "job")
    candidate_id_safe = safe_identifier(str(parsed_resume.get("name") or ""), "cand")
    jd_skills = " ".join(parsed_jd.get("expanded_required_skills") or parsed_jd.get("required_skills") or [])
    resume_skills = " ".join(parsed_resume.get("skills") or [])
    snippet_text = " ".join(str((item or {}).get("text") or "") for item in (evidence_snippets or [])[:4] if isinstance(item, dict))
    reason_text = " ".join(str(item or "") for item in (screening_reasons or [])[:4])

    anchors_query = _build_query([parsed_jd.get("job_title"), jd_skills, reason_text])
    positive_query = _build_query([parsed_jd.get("job_title"), jd_skills, resume_skills, snippet_text])
    counter_query = _build_query([parsed_jd.get("job_title"), "缺少 证据 不足", reason_text, resume_skills])
    missing_query = _build_query([parsed_jd.get("job_title"), jd_skills, "缺少 未覆盖", reason_text])
    historical_query = _build_query([parsed_jd.get("job_title"), jd_skills, resume_skills, snippet_text])
    risk_query = _build_query([parsed_jd.get("job_title"), "风险 核验 争议", reason_text])

    anchors = []
    counter = []
    missing = []
    positive = []
    historical = []
    risks = []

    try:
        if rag_feature_enabled("semantic_anchors", config):
            anchors = retrieve_for_semantic_anchors(
                anchors_query,
                top_k=(config.get("top_k") or {}).get("semantic_anchors", 4),
                role_family=role_family,
                job_id_safe=job_id_safe,
                runtime_config=config,
            )
    except Exception:
        anchors = []

    try:
        if rag_feature_enabled("counter_evidence", config):
            counter = retrieve_for_counter_evidence(
                counter_query,
                top_k=(config.get("top_k") or {}).get("counter_evidence", 4),
                role_family=role_family,
                job_id_safe=job_id_safe,
                candidate_id_safe=candidate_id_safe,
                runtime_config=config,
            )
    except Exception:
        counter = []

    try:
        if rag_feature_enabled("missing_evidence", config):
            missing = retrieve_for_missing_evidence(
                missing_query,
                top_k=(config.get("top_k") or {}).get("missing_evidence", 4),
                role_family=role_family,
                job_id_safe=job_id_safe,
                runtime_config=config,
            )
    except Exception:
        missing = []

    try:
        if rag_feature_enabled("evidence_grounding", config):
            positive = retrieve_for_evidence_grounding(
                positive_query,
                top_k=(config.get("top_k") or {}).get("evidence_grounding", 4),
                role_family=role_family,
                job_id_safe=job_id_safe,
                candidate_id_safe=candidate_id_safe,
                runtime_config=config,
            )
    except Exception:
        positive = []

    try:
        if rag_feature_enabled("historical_grounding", config):
            historical = retrieve_for_historical_grounding(
                historical_query,
                top_k=(config.get("top_k") or {}).get("historical_grounding", 4),
                role_family=role_family,
                job_id_safe=job_id_safe,
                runtime_config=config,
            )
    except Exception:
        historical = []

    try:
        if rag_feature_enabled("risk_grounding", config):
            risks = retrieve_for_risk_grounding(
                risk_query,
                top_k=(config.get("top_k") or {}).get("risk_grounding", 4),
                role_family=role_family,
                job_id_safe=job_id_safe,
                candidate_id_safe=candidate_id_safe,
                runtime_config=config,
            )
    except Exception:
        risks = []

    return {
        "enabled": True,
        "reason": "vector_store retrieval applied",
        "jd_semantic_anchors": _summarize_hits(anchors),
        "positive_evidence": _summarize_hits(positive),
        "counter_evidence": _summarize_hits(counter),
        "missing_evidence": _summarize_hits(missing),
        "historical_case_grounding": _summarize_hits(historical),
        "risk_case_grounding": _summarize_hits(risks),
        "queries": {
            "anchors": anchors_query,
            "positive": positive_query,
            "counter": counter_query,
            "missing": missing_query,
            "historical": historical_query,
            "risk": risk_query,
        },
    }


def default_vector_store_path() -> str:
    return str(resolve_vector_store_path(None))
