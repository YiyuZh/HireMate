"""AI 二次审核建议层（本地 stub 版）。

定位：
- 规则评分器仍是主评分器。
- 本模块仅在规则结果产出后，给出结构化二次审核建议。
- 本地阶段默认不强制真实 API 调用，优先返回可追踪的 mock/stub 建议。
"""

from __future__ import annotations

from typing import Any


def _default_ai_reviewer_config() -> dict[str, Any]:
    return {
        "enable_ai_reviewer": False,
        "ai_reviewer_mode": "off",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_base": "",
        "api_key_env_name": "OPENAI_API_KEY",
        "capabilities": {
            "add_evidence_snippets": True,
            "organize_timeline": True,
            "suggest_risk_adjustment": False,
            "suggest_score_adjustment": False,
            "generate_review_summary": True,
        },
        "score_adjustment_limit": {
            "max_delta_per_dimension": 1,
            "allow_break_hard_thresholds": False,
            "allow_direct_recommendation_change": False,
        },
    }


def _normalize_ai_reviewer_config(scoring_config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = scoring_config or {}
    ai_cfg = cfg.get("ai_reviewer") if isinstance(cfg.get("ai_reviewer"), dict) else {}
    defaults = _default_ai_reviewer_config()
    return {
        **defaults,
        **ai_cfg,
        "capabilities": {**defaults["capabilities"], **(ai_cfg.get("capabilities") or {})},
        "score_adjustment_limit": {**defaults["score_adjustment_limit"], **(ai_cfg.get("score_adjustment_limit") or {})},
    }


def _empty_ai_review_output(reason: str = "AI reviewer disabled") -> dict[str, Any]:
    return {
        "enabled": False,
        "mode": "off",
        "review_summary": "",
        "evidence_updates": [],
        "timeline_updates": [],
        "score_adjustments": [],
        "risk_adjustment": {},
        "recommended_action": "no_action",
        "meta": {
            "source": "stub",
            "reason": reason,
        },
    }


def run_ai_reviewer(
    parsed_jd: dict[str, Any],
    parsed_resume: dict[str, Any],
    role_profile: dict[str, Any],
    scoring_config: dict[str, Any] | None,
    score_details: dict[str, Any],
    risk_result: dict[str, Any],
    screening_result: dict[str, Any],
    evidence_snippets: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """基于规则结果生成 AI 二次审核建议（结构化 JSON）。"""
    ai_cfg = _normalize_ai_reviewer_config(scoring_config)
    if not ai_cfg.get("enable_ai_reviewer") or ai_cfg.get("ai_reviewer_mode") == "off":
        return _empty_ai_review_output("AI reviewer not enabled")

    caps = ai_cfg.get("capabilities") or {}
    limit_cfg = ai_cfg.get("score_adjustment_limit") or {}

    name = (parsed_resume.get("name") or "候选人").strip()
    result_text = screening_result.get("screening_result", "")
    risk_level = str(risk_result.get("risk_level", "unknown")).lower()

    if result_text == "推荐进入下一轮":
        action = "proceed"
    elif result_text == "建议人工复核":
        action = "manual_review"
    else:
        action = "hold"

    review_summary = ""
    if caps.get("generate_review_summary", True):
        review_summary = (
            f"AI二次审阅（{ai_cfg.get('ai_reviewer_mode')}）："
            f"{name} 当前规则结论为「{result_text or '未知'}」，"
            f"风险等级 {risk_level}，建议动作：{action}。"
        )

    evidence_updates: list[dict[str, str]] = []
    if caps.get("add_evidence_snippets", False):
        for item in (evidence_snippets or [])[:2]:
            txt = str(item.get("text") or "").strip()
            src = str(item.get("source") or "未知来源")
            if txt:
                evidence_updates.append({"source": src, "text": txt, "note": "AI建议补充引用"})

    timeline_updates: list[dict[str, str]] = []
    if caps.get("organize_timeline", False):
        grad = (parsed_resume.get("graduation_date") or "").strip()
        if grad:
            timeline_updates.append({"label": "毕业时间", "value": grad})

    score_adjustments: list[dict[str, Any]] = []
    if caps.get("suggest_score_adjustment", False):
        max_delta = int(limit_cfg.get("max_delta_per_dimension", 1) or 1)
        score_adjustments.append(
            {
                "dimension": "表达完整度",
                "suggested_delta": 0,
                "max_delta": max_delta,
                "reason": "本地 stub 默认不主动改分，仅保留接口。",
            }
        )

    risk_adjustment: dict[str, Any] = {}
    if caps.get("suggest_risk_adjustment", False):
        risk_adjustment = {
            "suggested_risk_level": risk_level,
            "reason": "本地 stub 默认保持规则风控结论，仅提供结构化占位。",
        }

    return {
        "enabled": True,
        "mode": ai_cfg.get("ai_reviewer_mode", "suggest_only"),
        "review_summary": review_summary,
        "evidence_updates": evidence_updates,
        "timeline_updates": timeline_updates,
        "score_adjustments": score_adjustments,
        "risk_adjustment": risk_adjustment,
        "recommended_action": action,
        "meta": {
            "source": "stub",
            "provider": ai_cfg.get("provider"),
            "model": ai_cfg.get("model"),
            "role_template": role_profile.get("profile_name", "通用岗位模板"),
            "allow_break_hard_thresholds": bool(limit_cfg.get("allow_break_hard_thresholds", False)),
            "allow_direct_recommendation_change": bool(limit_cfg.get("allow_direct_recommendation_change", False)),
        },
    }
