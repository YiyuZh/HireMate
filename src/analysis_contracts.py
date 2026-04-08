"""Unified analysis payload contracts for Resume Intelligence Pipeline."""

from __future__ import annotations

from typing import Any


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
        "meta": {
            "reason": reason,
        },
    }


def normalize_confidence(value: float | int | None) -> float:
    try:
        val = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, val))


def build_analysis_payload(
    *,
    analysis_mode: str,
    ocr_confidence: float,
    structure_confidence: float,
    parse_confidence: float,
    candidate_profile: dict[str, Any],
    evidence_for: list[dict[str, Any]],
    evidence_against: list[dict[str, Any]],
    missing_info_points: list[str],
    timeline_risks: list[str],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "analysis_mode": str(analysis_mode or "normal"),
        "ocr_confidence": normalize_confidence(ocr_confidence),
        "structure_confidence": normalize_confidence(structure_confidence),
        "parse_confidence": normalize_confidence(parse_confidence),
        "candidate_profile": candidate_profile or {},
        "evidence_for": evidence_for or [],
        "evidence_against": evidence_against or [],
        "missing_info_points": missing_info_points or [],
        "timeline_risks": timeline_risks or [],
        "meta": meta or {},
    }
