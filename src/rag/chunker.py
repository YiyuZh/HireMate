from __future__ import annotations

from copy import deepcopy
from hashlib import sha1
import json
from typing import Any

from src.role_profiles import build_default_scoring_config
from src.scorer import hydrate_representative_evidence

from .metadata import (
    build_chunk_metadata,
    extract_skill_tags_from_text,
    infer_language,
    infer_seniority,
    normalize_skill_tags,
    resolve_role_family,
    safe_identifier,
)


def _normalize_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return "；".join(parts)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value or "").strip()


def _make_chunk(
    *,
    text: str,
    metadata: dict[str, Any],
    document_id: str = "",
) -> dict[str, Any]:
    clean_text = _normalize_text(text)
    if not clean_text:
        return {}
    label = str(metadata.get("chunk_label") or metadata.get("source_type") or "chunk").strip()
    base_key = "|".join(
        [
            str(document_id or ""),
            str(metadata.get("source_type") or ""),
            label,
            clean_text,
        ]
    )
    chunk_id = f"chunk-{sha1(base_key.encode('utf-8')).hexdigest()[:16]}"
    return {
        "chunk_id": chunk_id,
        "document_id": str(document_id or "").strip(),
        "text": clean_text,
        "metadata": metadata,
    }


def _build_scoring_guidance_text(parsed_jd: dict[str, Any], scoring_config: dict[str, Any]) -> str:
    weights = scoring_config.get("weights") if isinstance(scoring_config.get("weights"), dict) else {}
    thresholds = scoring_config.get("screening_thresholds") if isinstance(scoring_config.get("screening_thresholds"), dict) else {}
    hard_thresholds = scoring_config.get("hard_thresholds") if isinstance(scoring_config.get("hard_thresholds"), dict) else {}
    risk_focus = scoring_config.get("risk_focus") if isinstance(scoring_config.get("risk_focus"), list) else []
    parts: list[str] = []

    if weights:
        parts.append("基础权重：" + "；".join(f"{key}:{value}" for key, value in weights.items()))
    if thresholds:
        parts.append("筛选门槛：" + "；".join(f"{key}:{value}" for key, value in thresholds.items()))
    if hard_thresholds:
        parts.append("硬门槛：" + "；".join(f"{key}:{value}" for key, value in hard_thresholds.items()))
    if risk_focus:
        parts.append("重点风险关注：" + "；".join(str(item) for item in risk_focus if str(item).strip()))
    if parsed_jd.get("internship_requirement"):
        parts.append(f"实习要求：{parsed_jd.get('internship_requirement')}")
    return "\n".join(part for part in parts if part).strip()


def build_chunks_from_jd(
    jd_text: str,
    parsed_jd: dict[str, Any] | None = None,
    *,
    job_id: str = "",
) -> list[dict[str, Any]]:
    payload = parsed_jd if isinstance(parsed_jd, dict) else {}
    role_family = resolve_role_family(payload)
    scoring_cfg = payload.get("scoring_config") if isinstance(payload.get("scoring_config"), dict) else {}
    if not scoring_cfg:
        scoring_cfg = build_default_scoring_config(role_family)

    required_skills = normalize_skill_tags(payload.get("required_skills") if isinstance(payload.get("required_skills"), list) else [])
    bonus_skills = normalize_skill_tags(payload.get("bonus_skills") if isinstance(payload.get("bonus_skills"), list) else [])
    skill_tags = normalize_skill_tags([*required_skills, *bonus_skills])
    job_id_safe = safe_identifier(job_id or payload.get("job_title") or jd_text[:32], "job")
    seniority = infer_seniority(payload.get("job_title"), payload.get("internship_requirement"))
    document_id = job_id_safe or "job-unknown"
    base_md = {
        "role_family": role_family,
        "skill_tags": skill_tags,
        "seniority": seniority,
        "job_id_safe": job_id_safe,
        "candidate_id_safe": "",
    }
    chunks: list[dict[str, Any]] = []

    for chunk in [
        _make_chunk(
            text=jd_text,
            document_id=document_id,
            metadata=build_chunk_metadata(
                source_type="jd",
                language=infer_language(jd_text),
                chunk_label="原始JD",
                created_from="jd.raw_text",
                **base_md,
            ),
        ),
        _make_chunk(
            text="；".join(required_skills),
            document_id=document_id,
            metadata=build_chunk_metadata(
                source_type="jd",
                language="zh-en",
                chunk_label="JD必备技能",
                created_from="parsed_jd.required_skills",
                **base_md,
            ),
        ) if required_skills else {},
        _make_chunk(
            text="；".join(bonus_skills),
            document_id=document_id,
            metadata=build_chunk_metadata(
                source_type="jd",
                language="zh-en",
                chunk_label="JD加分技能",
                created_from="parsed_jd.bonus_skills",
                **base_md,
            ),
        ) if bonus_skills else {},
    ]:
        if chunk:
            chunks.append(chunk)

    role_profile_text = "\n".join(
        line for line in [
            f"岗位模板：{role_family}",
            f"JD岗位名称：{payload.get('job_title') or ''}",
            f"学历要求：{payload.get('degree_requirement') or '未明确'}",
            f"专业偏好：{payload.get('major_preference') or '未明确'}",
            f"能力要求：{'；'.join(payload.get('competency_requirements') or []) or '未明确'}",
        ] if str(line).strip()
    )
    role_chunk = _make_chunk(
        text=role_profile_text,
        document_id=document_id,
        metadata=build_chunk_metadata(
            source_type="role_profile",
            language=infer_language(role_profile_text),
            chunk_label="岗位模板与画像",
            created_from="parsed_jd + role_profile",
            **base_md,
        ),
    )
    if role_chunk:
        chunks.append(role_chunk)

    scoring_guidance = _build_scoring_guidance_text(payload, scoring_cfg)
    rubric_chunk = _make_chunk(
        text=scoring_guidance,
        document_id=document_id,
        metadata=build_chunk_metadata(
            source_type="rubric",
            language=infer_language(scoring_guidance),
            chunk_label="评分细则与门槛",
            created_from="parsed_jd.scoring_config",
            **base_md,
        ),
    )
    if rubric_chunk:
        chunks.append(rubric_chunk)

    return chunks


def build_chunks_from_resume(
    resume_text: str,
    parsed_resume: dict[str, Any] | None = None,
    *,
    parsed_jd: dict[str, Any] | None = None,
    candidate_id: str = "",
    job_id: str = "",
) -> list[dict[str, Any]]:
    payload = parsed_resume if isinstance(parsed_resume, dict) else {}
    jd_payload = parsed_jd if isinstance(parsed_jd, dict) else {}
    role_family = resolve_role_family(jd_payload)
    base_skill_tags = normalize_skill_tags(payload.get("skills") if isinstance(payload.get("skills"), list) else [])
    skill_tags = normalize_skill_tags([*base_skill_tags, *extract_skill_tags_from_text(resume_text, extra_candidates=base_skill_tags)])
    candidate_anchor = candidate_id or payload.get("name") or resume_text[:32]
    candidate_id_safe = safe_identifier(candidate_anchor, "cand")
    job_id_safe = safe_identifier(job_id or jd_payload.get("job_title") or "", "job")
    seniority = infer_seniority(jd_payload.get("job_title"), jd_payload.get("internship_requirement"), resume_text)
    document_id = candidate_id_safe or "candidate-unknown"
    base_md = {
        "role_family": role_family,
        "skill_tags": skill_tags,
        "seniority": seniority,
        "job_id_safe": job_id_safe,
        "candidate_id_safe": candidate_id_safe,
    }
    chunks: list[dict[str, Any]] = []

    education_text = "；".join(
        part for part in [
            payload.get("education"),
            payload.get("degree"),
            payload.get("major"),
            payload.get("graduation_date"),
        ] if _normalize_text(part)
    )
    if education_text:
        chunk = _make_chunk(
            text=education_text,
            document_id=document_id,
            metadata=build_chunk_metadata(
                source_type="resume_fragment",
                language=infer_language(education_text),
                chunk_label="教育背景",
                created_from="parsed_resume.education",
                **base_md,
            ),
        )
        if chunk:
            chunks.append(chunk)

    for index, fragment in enumerate(payload.get("internships") or [], start=1):
        raw_text = _normalize_text(fragment.get("raw_text") if isinstance(fragment, dict) else fragment)
        chunk = _make_chunk(
            text=raw_text,
            document_id=document_id,
            metadata=build_chunk_metadata(
                source_type="resume_fragment",
                language=infer_language(raw_text),
                chunk_label=f"实习经历 {index}",
                created_from=f"parsed_resume.internships[{index - 1}]",
                **base_md,
            ),
        )
        if chunk:
            chunks.append(chunk)

    for index, fragment in enumerate(payload.get("projects") or [], start=1):
        raw_text = _normalize_text(fragment.get("raw_text") if isinstance(fragment, dict) else fragment)
        chunk = _make_chunk(
            text=raw_text,
            document_id=document_id,
            metadata=build_chunk_metadata(
                source_type="resume_fragment",
                language=infer_language(raw_text),
                chunk_label=f"项目经历 {index}",
                created_from=f"parsed_resume.projects[{index - 1}]",
                **base_md,
            ),
        )
        if chunk:
            chunks.append(chunk)

    if base_skill_tags:
        chunk = _make_chunk(
            text="；".join(base_skill_tags),
            document_id=document_id,
            metadata=build_chunk_metadata(
                source_type="resume_fragment",
                language="zh-en",
                chunk_label="技能清单",
                created_from="parsed_resume.skills",
                **base_md,
            ),
        )
        if chunk:
            chunks.append(chunk)

    summary_parts = []
    if payload.get("awards"):
        summary_parts.append(f"奖项：{'；'.join(str(item) for item in payload.get('awards') or [])}")
    if payload.get("languages"):
        summary_parts.append(f"语言：{'；'.join(str(item) for item in payload.get('languages') or [])}")
    if resume_text:
        summary_parts.append(f"简历原文长度：{len(resume_text)}")
    summary_chunk = _make_chunk(
        text="\n".join(part for part in summary_parts if part).strip(),
        document_id=document_id,
        metadata=build_chunk_metadata(
            source_type="resume_fragment",
            language="zh-en",
            chunk_label="总结/补充信息",
            created_from="parsed_resume.awards + parsed_resume.languages",
            **base_md,
        ),
    )
    if summary_chunk:
        chunks.append(summary_chunk)

    return chunks


def build_chunks_from_evidence(
    *,
    evidence_snippets: list[dict[str, Any]] | None = None,
    score_details: dict[str, Any] | None = None,
    screening_reasons: list[str] | None = None,
    parsed_jd: dict[str, Any] | None = None,
    candidate_id: str = "",
    job_id: str = "",
) -> list[dict[str, Any]]:
    jd_payload = parsed_jd if isinstance(parsed_jd, dict) else {}
    hydrated_scores = hydrate_representative_evidence(deepcopy(score_details if isinstance(score_details, dict) else {}))
    role_family = resolve_role_family(jd_payload)
    candidate_id_safe = safe_identifier(candidate_id, "cand")
    job_id_safe = safe_identifier(job_id or jd_payload.get("job_title") or "", "job")
    skill_tags = normalize_skill_tags([*(jd_payload.get("required_skills") or []), *(jd_payload.get("bonus_skills") or [])])
    seniority = infer_seniority(jd_payload.get("job_title"), jd_payload.get("internship_requirement"))
    document_id = candidate_id_safe or job_id_safe or "evidence-unknown"
    base_md = {
        "role_family": role_family,
        "skill_tags": skill_tags,
        "seniority": seniority,
        "job_id_safe": job_id_safe,
        "candidate_id_safe": candidate_id_safe,
    }
    chunks: list[dict[str, Any]] = []
    seen_text: set[str] = set()

    def _append_unique(chunk: dict[str, Any]) -> None:
        if not chunk:
            return
        text_key = _normalize_text(chunk.get("text"))
        if not text_key or text_key in seen_text:
            return
        seen_text.add(text_key)
        chunks.append(chunk)

    for index, snippet in enumerate(evidence_snippets or [], start=1):
        if not isinstance(snippet, dict):
            snippet = {"text": str(snippet or ""), "source": "其他"}
        snippet_text = _normalize_text(snippet.get("text"))
        label_parts = ["关键证据"]
        if snippet.get("source"):
            label_parts.append(str(snippet.get("source")))
        if snippet.get("tag"):
            label_parts.append(str(snippet.get("tag")))
        _append_unique(
            _make_chunk(
                text=snippet_text,
                document_id=document_id,
                metadata=build_chunk_metadata(
                    source_type="evidence",
                    language=infer_language(snippet_text),
                    chunk_label=" · ".join(label_parts[:3]) or f"关键证据 {index}",
                    created_from=f"evidence_snippets[{index - 1}]",
                    **base_md,
                ),
            )
        )

    for dimension, detail in (hydrated_scores or {}).items():
        if not isinstance(detail, dict):
            continue
        representative = detail.get("representative_evidence") if isinstance(detail.get("representative_evidence"), dict) else {}
        representative_text = _normalize_text(representative.get("display_text") or representative.get("text") or representative.get("raw_text"))
        _append_unique(
            _make_chunk(
                text=representative_text,
                document_id=document_id,
                metadata=build_chunk_metadata(
                    source_type="evidence",
                    language=infer_language(representative_text),
                    chunk_label=f"代表证据 · {dimension}",
                    created_from=f"score_details.{dimension}.representative_evidence",
                    **base_md,
                ),
            )
        )

        reason_text = _normalize_text(detail.get("reason"))
        _append_unique(
            _make_chunk(
                text=reason_text,
                document_id=document_id,
                metadata=build_chunk_metadata(
                    source_type="rubric",
                    language=infer_language(reason_text),
                    chunk_label=f"评分说明 · {dimension}",
                    created_from=f"score_details.{dimension}.reason",
                    **base_md,
                ),
            )
        )

    for index, reason in enumerate(screening_reasons or [], start=1):
        reason_text = _normalize_text(reason)
        _append_unique(
            _make_chunk(
                text=reason_text,
                document_id=document_id,
                metadata=build_chunk_metadata(
                    source_type="evidence",
                    language=infer_language(reason_text),
                    chunk_label=f"结论原因 {index}",
                    created_from="screening_decision.screening_reasons",
                    **base_md,
                ),
            )
        )

    return chunks
