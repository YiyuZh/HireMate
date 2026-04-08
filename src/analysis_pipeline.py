"""Resume Intelligence Pipeline: unified analysis payload."""

from __future__ import annotations

from typing import Any

from src.analysis_contracts import build_analysis_payload, empty_analysis_payload, normalize_confidence
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


def run_analysis_pipeline(
    *,
    parsed_resume: dict[str, Any],
    parsed_jd: dict[str, Any] | None = None,
    extract_result: dict[str, Any] | None = None,
    normalized_text: str = "",
    raw_text: str = "",
    evidence_snippets: list[dict[str, Any]] | None = None,
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

    evidence_for = evidence_snippets or []
    evidence_against: list[dict[str, Any]] = []
    missing_info_points = candidate_profile.get("missing_info_points") or []
    timeline_risks = candidate_profile.get("timeline_risks") or []

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
        meta={
            "quality": quality,
            "analysis_score": float(analysis.get("score") or 0.0),
            "analysis_length": int(analysis.get("length") or 0),
            "analysis_keywords": int(analysis.get("keyword_hits") or 0),
            "parsed_jd_present": bool(parsed_jd),
        },
    )
