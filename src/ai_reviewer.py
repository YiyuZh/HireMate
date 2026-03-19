"""AI 二次审核建议层（本地 stub 版）。

定位：
- 规则评分器仍是主评分器。
- 本模块仅在规则结果产出后，给出结构化二次审核建议。
- 本地阶段默认不强制真实 API 调用，优先返回可追踪的 mock/stub 建议。
"""

from __future__ import annotations

import json
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


def get_ai_reviewer_output_schema(mode: str = "suggest_only") -> dict[str, Any]:
    """定义 AI reviewer 结构化输出 schema（预留真实 API 对接）。"""
    mode_norm = (mode or "suggest_only").strip().lower()
    mode_note = {
        "suggest_only": "仅输出建议，不可直接改写规则结果。",
        "bounded_override": "可给出受限修正建议，必须受 max_delta/硬门槛限制约束。",
        "human_approve": "仅输出待人工确认建议，应用动作必须由人工触发。",
    }.get(mode_norm, "默认建议模式。")

    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "review_summary",
            "evidence_updates",
            "timeline_updates",
            "score_adjustments",
            "risk_adjustment",
            "recommended_action",
        ],
        "mode_note": mode_note,
        "properties": {
            "review_summary": {"type": "string"},
            "evidence_updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["source", "text"],
                    "properties": {
                        "source": {"type": "string"},
                        "text": {"type": "string"},
                        "note": {"type": "string"},
                    },
                },
            },
            "timeline_updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["label", "value"],
                    "properties": {
                        "label": {"type": "string"},
                        "value": {"type": "string"},
                        "note": {"type": "string"},
                    },
                },
            },
            "score_adjustments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["dimension", "suggested_delta", "reason"],
                    "properties": {
                        "dimension": {"type": "string"},
                        "suggested_delta": {"type": "integer"},
                        "max_delta": {"type": "integer"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "risk_adjustment": {
                "type": "object",
                "properties": {
                    "suggested_risk_level": {"type": "string", "enum": ["low", "medium", "high", "unknown"]},
                    "reason": {"type": "string"},
                },
            },
            "recommended_action": {
                "type": "string",
                "enum": ["proceed", "manual_review", "hold", "no_action"],
            },
        },
    }


def build_ai_reviewer_prompt(
    parsed_jd: dict[str, Any],
    parsed_resume: dict[str, Any],
    role_profile: dict[str, Any],
    scoring_config: dict[str, Any] | None,
    score_details: dict[str, Any],
    risk_result: dict[str, Any],
    screening_result: dict[str, Any],
    evidence_snippets: list[dict[str, Any]] | None,
) -> str:
    """构建 AI reviewer Prompt（预留真实 API 调用）。"""
    ai_cfg = _normalize_ai_reviewer_config(scoring_config)
    mode = ai_cfg.get("ai_reviewer_mode", "suggest_only")
    schema = get_ai_reviewer_output_schema(mode)
    caps = ai_cfg.get("capabilities") or {}
    limits = ai_cfg.get("score_adjustment_limit") or {}

    payload = {
        "role_profile": role_profile.get("profile_name", "通用岗位模板"),
        "mode": mode,
        "capabilities": caps,
        "limits": limits,
        "parsed_jd": parsed_jd,
        "parsed_resume": parsed_resume,
        "score_details": score_details,
        "risk_result": risk_result,
        "screening_result": screening_result,
        "evidence_snippets": evidence_snippets or [],
    }

    mode_rules = {
        "suggest_only": "你只能给出建议，不得要求系统自动改分或改结论。",
        "bounded_override": (
            "你可给出修正建议，但必须受 max_delta_per_dimension 约束，"
            "且当 allow_break_hard_thresholds=false 时不得建议突破硬门槛。"
        ),
        "human_approve": "你可以给出完整建议，但必须标记为‘待人工确认’后才可生效。",
    }

    return (
        "你是招聘流程中的 AI 二次审核员，不是主评分器。\n"
        "你的任务是审核规则评分结果，而不是从零重打分。\n"
        f"当前审核模式：{mode}。{mode_rules.get(mode, mode_rules['suggest_only'])}\n"
        "请严格输出 JSON，不要输出额外解释文本。\n"
        "输出 JSON 必须满足以下 schema：\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "以下是审核输入：\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


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

    prompt_preview = build_ai_reviewer_prompt(
        parsed_jd=parsed_jd,
        parsed_resume=parsed_resume,
        role_profile=role_profile,
        scoring_config=scoring_config,
        score_details=score_details,
        risk_result=risk_result,
        screening_result=screening_result,
        evidence_snippets=evidence_snippets,
    )

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
            "prompt_preview": prompt_preview,
            "schema": get_ai_reviewer_output_schema(ai_cfg.get("ai_reviewer_mode", "suggest_only")),
        },
    }
