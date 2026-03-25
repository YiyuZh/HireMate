from __future__ import annotations

from hashlib import sha1
import re
from typing import Any

from src.role_profiles import DEFAULT_PROFILE, detect_role_profile, get_profile_by_name


RAG_CHUNK_VERSION = "rag_chunk_v1"
SOURCE_TYPES = {"jd", "role_profile", "resume_fragment", "evidence", "rubric"}
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+/.#-]*|[\u4e00-\u9fff]{2,}")


def _slugify(value: str, *, max_len: int = 24) -> str:
    text = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", str(value or "").strip())
    text = re.sub(r"-{2,}", "-", text).strip("-").lower()
    return text[:max_len].strip("-")


def safe_identifier(raw_value: str | None, prefix: str) -> str:
    value = str(raw_value or "").strip()
    if not value:
        return ""
    digest = sha1(value.encode("utf-8")).hexdigest()[:12]
    slug = _slugify(value, max_len=16)
    return f"{prefix}-{slug}-{digest}" if slug else f"{prefix}-{digest}"


def infer_language(text: str) -> str:
    raw = str(text or "")
    zh_hits = len(re.findall(r"[\u4e00-\u9fff]", raw))
    en_hits = len(re.findall(r"[A-Za-z]", raw))
    if zh_hits and en_hits:
        return "zh-en"
    if zh_hits:
        return "zh"
    if en_hits:
        return "en"
    return "unknown"


def infer_seniority(*parts: str) -> str:
    blob = " ".join(str(part or "") for part in parts).lower()
    if any(keyword in blob for keyword in ["实习", "intern"]):
        return "intern"
    if any(keyword in blob for keyword in ["校招", "应届", "junior", "初级", "助理"]):
        return "junior"
    if any(keyword in blob for keyword in ["高级", "资深", "senior"]):
        return "senior"
    if any(keyword in blob for keyword in ["专家", "leader", "负责人", "总监"]):
        return "lead"
    return "unspecified"


def normalize_skill_tags(values: list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        clean = str(value or "").strip()
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(clean)
    return normalized


def extract_skill_tags_from_text(text: str, *, extra_candidates: list[str] | None = None) -> list[str]:
    tokens = {token.lower(): token for token in _TOKEN_RE.findall(str(text or ""))}
    candidates = normalize_skill_tags(extra_candidates)
    hits: list[str] = []
    for candidate in candidates:
        if candidate.lower() in tokens and candidate not in hits:
            hits.append(candidate)
    return hits


def resolve_role_family(parsed_jd: dict[str, Any] | None = None, *, fallback_profile_name: str = "") -> str:
    if isinstance(parsed_jd, dict) and parsed_jd:
        scoring_cfg = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
        explicit_name = str(scoring_cfg.get("role_template") or scoring_cfg.get("profile_name") or "").strip()
        if explicit_name:
            return get_profile_by_name(explicit_name).get("profile_name", explicit_name)
        detected = detect_role_profile(parsed_jd)
        if isinstance(detected, dict) and detected:
            return str(detected.get("profile_name") or DEFAULT_PROFILE.get("profile_name") or "").strip()
    if fallback_profile_name:
        return str(get_profile_by_name(fallback_profile_name).get("profile_name") or fallback_profile_name).strip()
    return str(DEFAULT_PROFILE.get("profile_name") or "通用岗位模板")


def build_chunk_metadata(
    *,
    source_type: str,
    role_family: str = "",
    skill_tags: list[str] | tuple[str, ...] | set[str] | None = None,
    seniority: str = "",
    language: str = "",
    job_id_safe: str = "",
    candidate_id_safe: str = "",
    chunk_label: str = "",
    chunk_version: str = RAG_CHUNK_VERSION,
    created_from: str = "",
) -> dict[str, Any]:
    normalized_source = str(source_type or "").strip()
    if normalized_source not in SOURCE_TYPES:
        raise ValueError(f"unsupported source_type: {normalized_source}")

    return {
        "source_type": normalized_source,
        "role_family": str(role_family or "").strip(),
        "skill_tags": normalize_skill_tags(skill_tags),
        "seniority": str(seniority or "unspecified").strip() or "unspecified",
        "language": str(language or "unknown").strip() or "unknown",
        "job_id_safe": str(job_id_safe or "").strip(),
        "candidate_id_safe": str(candidate_id_safe or "").strip(),
        "chunk_label": str(chunk_label or "").strip(),
        "chunk_version": str(chunk_version or RAG_CHUNK_VERSION).strip() or RAG_CHUNK_VERSION,
        "created_from": str(created_from or "").strip(),
    }


def ensure_chunk_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    payload = metadata if isinstance(metadata, dict) else {}
    source_type = str(payload.get("source_type") or "evidence").strip()
    if source_type not in SOURCE_TYPES:
        source_type = "evidence"
    return build_chunk_metadata(
        source_type=source_type,
        role_family=str(payload.get("role_family") or "").strip(),
        skill_tags=payload.get("skill_tags") if isinstance(payload.get("skill_tags"), (list, tuple, set)) else [],
        seniority=str(payload.get("seniority") or "unspecified").strip() or "unspecified",
        language=str(payload.get("language") or "unknown").strip() or "unknown",
        job_id_safe=str(payload.get("job_id_safe") or "").strip(),
        candidate_id_safe=str(payload.get("candidate_id_safe") or "").strip(),
        chunk_label=str(payload.get("chunk_label") or "").strip(),
        chunk_version=str(payload.get("chunk_version") or RAG_CHUNK_VERSION).strip() or RAG_CHUNK_VERSION,
        created_from=str(payload.get("created_from") or "").strip(),
    )


def metadata_has_required_fields(metadata: dict[str, Any] | None) -> bool:
    payload = ensure_chunk_metadata(metadata)
    required_fields = [
        "source_type",
        "role_family",
        "skill_tags",
        "seniority",
        "language",
        "job_id_safe",
        "candidate_id_safe",
        "chunk_label",
        "chunk_version",
        "created_from",
    ]
    return all(field in payload for field in required_fields)
