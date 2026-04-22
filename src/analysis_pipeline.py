"""Resume Intelligence Pipeline: build a grounded analysis payload."""

from __future__ import annotations

import hashlib
from typing import Any

from src.analysis_contracts import build_analysis_payload, empty_analysis_payload, normalize_confidence
from src.rag import build_full_grounding
from src.resume_intelligence import build_candidate_profile


def _confidence_from_quality(quality: str, analysis: dict[str, Any] | None) -> float:
    if not analysis:
        return 0.3 if (quality or "").lower() == "ok" else 0.15
    score = float(analysis.get("score") or 0.0)
    length = float(analysis.get("length") or 0.0)
    base = min(1.0, (score / 100.0) + (length / 8000.0))
    if (quality or "").lower() == "weak":
        base *= 0.6
    return normalize_confidence(base)


def _stable_evidence_id(prefix: str, source: str, text: str, index: int) -> str:
    digest = hashlib.sha1(f"{prefix}|{source}|{text}|{index}".encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{index + 1}_{digest}"


def _normalize_evidence_item(
    item: dict[str, Any] | str,
    *,
    prefix: str,
    index: int,
    default_source: str,
    default_label: str,
    default_tags: list[str] | None = None,
) -> dict[str, Any]:
    if isinstance(item, str):
        source = default_source
        text = item.strip()
        raw_text = text
        label = default_label
        tags = list(default_tags or [])
    else:
        source = str(item.get("source") or default_source).strip() or default_source
        text = str(item.get("display_text") or item.get("text") or item.get("value") or item.get("reason") or "").strip()
        raw_text = str(item.get("raw_text") or text).strip()
        label = str(item.get("label") or default_label).strip() or default_label
        tags = [str(tag).strip() for tag in (item.get("tags") or []) if str(tag).strip()]
        tag = str(item.get("tag") or "").strip()
        if tag and tag not in tags:
            tags.append(tag)
        if default_tags:
            for tag_value in default_tags:
                if tag_value not in tags:
                    tags.append(tag_value)
    evidence_id = str(item.get("evidence_id") if isinstance(item, dict) else "") or _stable_evidence_id(prefix, source, raw_text or text, index)
    return {
        "evidence_id": evidence_id,
        "source": source,
        "text": text,
        "raw_text": raw_text,
        "label": label,
        "tags": tags,
    }


def _grounding_to_evidence_items(items: Any, *, prefix: str, label: str, tags: list[str]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items if isinstance(items, list) else []):
        if not isinstance(item, dict):
            continue
        chunk_label = str(item.get("chunk_label") or item.get("source_type") or label).strip() or label
        evidence = _normalize_evidence_item(
            {
                "source": chunk_label,
                "text": str(item.get("text") or "").strip(),
                "label": label,
                "tags": [*tags, *[str(tag).strip() for tag in (item.get("skill_tags") or []) if str(tag).strip()]],
            },
            prefix=prefix,
            index=index,
            default_source=chunk_label,
            default_label=label,
        )
        if evidence.get("text"):
            normalized.append(evidence)
    return normalized


def _unique_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = f"{str(item.get('source') or '').lower()}|{str(item.get('text') or '').lower()}"
        if not str(item.get("text") or "").strip():
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _build_claim_candidates(
    evidence_for: list[dict[str, Any]],
    evidence_against: list[dict[str, Any]],
    missing_info_points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for index, item in enumerate(evidence_for[:5]):
        claims.append(
            {
                "claim_id": f"claim_support_{index + 1}",
                "type": "positive_evidence",
                "summary": str(item.get("text") or ""),
                "supporting_evidence_ids": [str(item.get("evidence_id") or "")],
                "opposing_evidence_ids": [],
                "support_status": "supported",
            }
        )
    for index, item in enumerate(evidence_against[:4]):
        claims.append(
            {
                "claim_id": f"claim_counter_{index + 1}",
                "type": "counter_evidence",
                "summary": str(item.get("text") or ""),
                "supporting_evidence_ids": [str(item.get("evidence_id") or "")],
                "opposing_evidence_ids": [],
                "support_status": "contradicted",
            }
        )
    for index, item in enumerate(missing_info_points[:4]):
        claims.append(
            {
                "claim_id": f"claim_missing_{index + 1}",
                "type": "missing_evidence",
                "summary": str(item.get("text") or ""),
                "supporting_evidence_ids": [str(item.get("evidence_id") or "")],
                "opposing_evidence_ids": [],
                "support_status": "missing_evidence",
            }
        )
    return claims


def _build_evidence_trace(
    evidence_for: list[dict[str, Any]],
    evidence_against: list[dict[str, Any]],
    missing_info_points: list[dict[str, Any]],
    timeline_risks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    for item in evidence_for:
        trace.append(
            {
                "evidence_id": item.get("evidence_id"),
                "bucket": "positive_evidence",
                "source": item.get("source"),
                "text": item.get("text"),
                "label": item.get("label"),
                "tags": item.get("tags") or [],
            }
        )
    for item in evidence_against:
        trace.append(
            {
                "evidence_id": item.get("evidence_id"),
                "bucket": "counter_evidence",
                "source": item.get("source"),
                "text": item.get("text"),
                "label": item.get("label"),
                "tags": item.get("tags") or [],
            }
        )
    for item in missing_info_points:
        trace.append(
            {
                "evidence_id": item.get("evidence_id"),
                "bucket": "missing_info",
                "source": item.get("source"),
                "text": item.get("text"),
                "label": item.get("label"),
                "tags": item.get("tags") or [],
            }
        )
    for item in timeline_risks:
        trace.append(
            {
                "evidence_id": item.get("evidence_id"),
                "bucket": "timeline_risk",
                "source": item.get("source"),
                "text": item.get("text"),
                "label": item.get("label"),
                "tags": item.get("tags") or [],
            }
        )
    return trace


def _build_abstain_reasons(
    *,
    analysis_mode: str,
    quality: str,
    parse_confidence: float,
    evidence_for: list[dict[str, Any]],
    missing_info_points: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    if analysis_mode == "manual_first":
        reasons.append("manual_first")
    elif analysis_mode == "weak_text":
        reasons.append("weak_text")
    if (quality or "").lower() == "weak":
        reasons.append("ocr_quality_weak")
    if parse_confidence < 0.35:
        reasons.append("parse_confidence_low")
    if not evidence_for:
        reasons.append("positive_evidence_sparse")
    if len(missing_info_points) >= 2:
        reasons.append("missing_core_information")
    deduped: list[str] = []
    for item in reasons:
        if item not in deduped:
            deduped.append(item)
    return deduped


def run_analysis_pipeline(
    *,
    parsed_resume: dict[str, Any],
    parsed_jd: dict[str, Any] | None = None,
    extract_result: dict[str, Any] | None = None,
    normalized_text: str = "",
    raw_text: str = "",
    evidence_snippets: list[dict[str, Any]] | None = None,
    score_details: dict[str, Any] | None = None,
    risk_result: dict[str, Any] | None = None,
    screening_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(parsed_resume, dict):
        return empty_analysis_payload("missing parsed resume")

    extract_result = extract_result if isinstance(extract_result, dict) else {}
    analysis = extract_result.get("quality_analysis") if isinstance(extract_result.get("quality_analysis"), dict) else {}
    quality = str(extract_result.get("quality") or "weak")
    ocr_confidence = _confidence_from_quality(quality, analysis)

    structure_signals = [
        bool(parsed_resume.get("education")),
        bool(parsed_resume.get("internships") or parsed_resume.get("projects")),
        bool(parsed_resume.get("skills")),
    ]
    structure_confidence = normalize_confidence(sum(1 for flag in structure_signals if flag) / 3.0)
    parse_confidence = normalize_confidence((ocr_confidence + structure_confidence) / 2.0)

    analysis_mode = "normal"
    if quality.lower() == "weak" or parse_confidence < 0.45:
        analysis_mode = "weak_text"
    if parse_confidence < 0.3:
        analysis_mode = "manual_first"

    candidate_profile = build_candidate_profile(
        parsed_resume,
        normalized_text=normalized_text,
        raw_text=raw_text,
    )

    positive_items = [
        _normalize_evidence_item(
            item,
            prefix="positive",
            index=index,
            default_source="resume",
            default_label="正向证据",
            default_tags=["正向证据"],
        )
        for index, item in enumerate(evidence_snippets or [])
    ]

    grounding_summary = build_full_grounding(
        parsed_jd=parsed_jd if isinstance(parsed_jd, dict) else {},
        parsed_resume=parsed_resume,
        evidence_snippets=evidence_snippets,
        screening_reasons=(screening_result or {}).get("screening_reasons") if isinstance(screening_result, dict) else [],
    )

    positive_items.extend(
        _grounding_to_evidence_items(
            (grounding_summary.get("positive_evidence") if isinstance(grounding_summary, dict) else []),
            prefix="positive_grounding",
            label="Grounding 命中",
            tags=["正向证据", "grounding"],
        )
    )

    evidence_against = _grounding_to_evidence_items(
        (grounding_summary.get("counter_evidence") if isinstance(grounding_summary, dict) else []),
        prefix="counter_grounding",
        label="反证",
        tags=["反证", "需复核"],
    )

    risk_points = (risk_result or {}).get("risk_points") if isinstance(risk_result, dict) else []
    for index, item in enumerate(risk_points if isinstance(risk_points, list) else []):
        evidence_against.append(
            _normalize_evidence_item(
                item,
                prefix="risk_point",
                index=index,
                default_source="risk",
                default_label="风险点",
                default_tags=["反证", "风险"],
            )
        )

    missing_info_points = [
        _normalize_evidence_item(
            item,
            prefix="missing",
            index=index,
            default_source="missing",
            default_label="缺失点",
            default_tags=["缺证"],
        )
        for index, item in enumerate(candidate_profile.get("missing_info_points") or [])
    ]
    missing_info_points.extend(
        _grounding_to_evidence_items(
            (grounding_summary.get("missing_evidence") if isinstance(grounding_summary, dict) else []),
            prefix="missing_grounding",
            label="缺失点",
            tags=["缺证", "grounding"],
        )
    )

    timeline_risks = [
        _normalize_evidence_item(
            item,
            prefix="timeline",
            index=index,
            default_source="timeline",
            default_label="时间线风险",
            default_tags=["时间线", "需复核"],
        )
        for index, item in enumerate(candidate_profile.get("timeline_risks") or [])
    ]

    if analysis_mode in {"weak_text", "manual_first"}:
        evidence_against.append(
            _normalize_evidence_item(
                {
                    "source": "ocr",
                    "text": "OCR / 解析质量偏弱，当前分析可信度受限，建议人工优先复核。",
                    "label": "解析质量风险",
                    "tags": ["反证", "需复核"],
                },
                prefix="quality_gate",
                index=0,
                default_source="ocr",
                default_label="解析质量风险",
            )
        )

    evidence_for = _unique_evidence(positive_items)
    evidence_against = _unique_evidence(evidence_against)
    missing_info_points = _unique_evidence(missing_info_points)
    timeline_risks = _unique_evidence(timeline_risks)

    abstain_reasons = _build_abstain_reasons(
        analysis_mode=analysis_mode,
        quality=quality,
        parse_confidence=parse_confidence,
        evidence_for=evidence_for,
        missing_info_points=missing_info_points,
    )

    claim_candidates = _build_claim_candidates(evidence_for, evidence_against, missing_info_points)
    evidence_trace = _build_evidence_trace(evidence_for, evidence_against, missing_info_points, timeline_risks)

    return build_analysis_payload(
        analysis_mode=analysis_mode,
        ocr_confidence=ocr_confidence,
        structure_confidence=structure_confidence,
        parse_confidence=parse_confidence,
        candidate_profile=candidate_profile,
        evidence_for=evidence_for,
        evidence_against=evidence_against,
        missing_info_points=missing_info_points,
        timeline_risks=timeline_risks,
        evidence_trace=evidence_trace,
        grounding_summary=grounding_summary if isinstance(grounding_summary, dict) else {},
        claim_candidates=claim_candidates,
        abstain_reasons=abstain_reasons,
        meta={
            "quality": quality,
            "analysis_score": float(analysis.get("score") or 0.0),
            "analysis_length": int(analysis.get("length") or 0),
            "analysis_keywords": int(analysis.get("keyword_hits") or 0),
            "parsed_jd_present": bool(parsed_jd),
            "score_details_present": bool(score_details),
            "risk_result_present": bool(risk_result),
            "screening_result_present": bool(screening_result),
        },
    )
