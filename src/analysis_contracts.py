"""Unified analysis payload contracts for the Resume Intelligence Pipeline."""

from __future__ import annotations

from typing import Any


def normalize_confidence(value: float | int | None) -> float:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, val))


def _list_of_dicts(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(dict(item))
            continue
        if item not in (None, ""):
            normalized.append({"text": str(item)})
    return normalized


def empty_analysis_payload(reason: str = "analysis disabled") -> dict[str, Any]:
    return {
        "analysis_mode": "manual_first",
        "ocr_confidence": 0.0,
        "structure_confidence": 0.0,
        "parse_confidence": 0.0,
        "candidate_profile": {},
        "evidence_for": [],
        "evidence_against": [],
        "missing_info_points": [],
        "timeline_risks": [],
        "evidence_trace": [],
        "grounding_summary": {
            "enabled": False,
            "reason": reason,
            "jd_semantic_anchors": [],
            "positive_evidence": [],
            "counter_evidence": [],
            "missing_evidence": [],
            "historical_case_grounding": [],
            "risk_case_grounding": [],
        },
        "claim_candidates": [],
        "abstain_reasons": [reason] if reason else [],
        "meta": {
            "reason": reason,
        },
    }


def build_analysis_payload(
    *,
    analysis_mode: str,
    ocr_confidence: float,
    structure_confidence: float,
    parse_confidence: float,
    candidate_profile: dict[str, Any],
    evidence_for: list[dict[str, Any]],
    evidence_against: list[dict[str, Any]],
    missing_info_points: list[dict[str, Any]],
    timeline_risks: list[dict[str, Any]],
    evidence_trace: list[dict[str, Any]] | None = None,
    grounding_summary: dict[str, Any] | None = None,
    claim_candidates: list[dict[str, Any]] | None = None,
    abstain_reasons: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "analysis_mode": str(analysis_mode or "normal"),
        "ocr_confidence": normalize_confidence(ocr_confidence),
        "structure_confidence": normalize_confidence(structure_confidence),
        "parse_confidence": normalize_confidence(parse_confidence),
        "candidate_profile": candidate_profile or {},
        "evidence_for": _list_of_dicts(evidence_for),
        "evidence_against": _list_of_dicts(evidence_against),
        "missing_info_points": _list_of_dicts(missing_info_points),
        "timeline_risks": _list_of_dicts(timeline_risks),
        "evidence_trace": _list_of_dicts(evidence_trace),
        "grounding_summary": grounding_summary if isinstance(grounding_summary, dict) else {},
        "claim_candidates": _list_of_dicts(claim_candidates),
        "abstain_reasons": [str(item).strip() for item in (abstain_reasons or []) if str(item).strip()],
        "meta": meta or {},
    }
