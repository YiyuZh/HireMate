"""AI secondary review layer with real API call + stub fallback."""

from __future__ import annotations

import json
import os
from time import perf_counter
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

OPENAI_DEFAULT_API_BASE = "https://api.openai.com/v1"
AI_REVIEWER_PROMPT_VERSION = "v1"
ALLOWED_RISK_LEVELS = {"low", "medium", "high", "unknown"}
ALLOWED_ACTIONS = {"proceed", "manual_review", "hold", "no_action"}


def get_ai_reviewer_prompt_version() -> str:
    return AI_REVIEWER_PROMPT_VERSION


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
        "score_adjustment_limit": {
            **defaults["score_adjustment_limit"],
            **(ai_cfg.get("score_adjustment_limit") or {}),
        },
    }


def get_ai_reviewer_output_schema(mode: str = "suggest_only") -> dict[str, Any]:
    """Return the structured AI reviewer schema."""
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
                    "additionalProperties": False,
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
                    "additionalProperties": False,
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
                    "additionalProperties": False,
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
                "additionalProperties": False,
                "properties": {
                    "suggested_risk_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "unknown"],
                    },
                    "reason": {"type": "string"},
                },
            },
            "recommended_action": {
                "type": "string",
                "enum": ["proceed", "manual_review", "hold", "no_action"],
            },
        },
    }


def _response_json_schema(mode: str = "suggest_only") -> dict[str, Any]:
    schema = dict(get_ai_reviewer_output_schema(mode))
    schema.pop("mode_note", None)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "hiremate_ai_reviewer_output",
            "strict": True,
            "schema": schema,
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
    """Build the reviewer prompt payload for API or debug preview."""
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
        "human_approve": "你可以给出完整建议，但必须标记为“待人工确认”后才可生效。",
    }

    return (
        "你是招聘流程中的 AI 二次审核员，不是主评分器。\n"
        "规则评分器仍然是主评分器；你只能基于已有规则结果给出二次审核建议。\n"
        "你不得输出人工最终结论，不得替代人工“通过 / 待复核 / 淘汰”按钮。\n"
        "你可以建议补充证据、补充时间线、调整风险、建议改分，但任何建议都必须先人工点击应用。\n"
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
            "prompt_version": AI_REVIEWER_PROMPT_VERSION,
            "generated_latency_ms": 0,
        },
    }


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        fragments: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                fragments.append(item["text"])
                continue
            text_block = item.get("text")
            if isinstance(text_block, dict) and isinstance(text_block.get("value"), str):
                fragments.append(text_block["value"])
        return "\n".join(part for part in fragments if part).strip()
    return ""


def _strip_json_wrappers(payload: str) -> str:
    text = (payload or "").strip()
    if not text:
        return text
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _build_openai_chat_url(api_base: str) -> str:
    base = (api_base or "").strip() or OPENAI_DEFAULT_API_BASE
    if base.endswith("/chat/completions"):
        return base

    parsed = urlparse.urlparse(base)
    path = parsed.path.rstrip("/")
    if not path:
        return base.rstrip("/") + "/v1/chat/completions"
    return base.rstrip("/") + "/chat/completions"


def _attach_runtime_meta(result: dict[str, Any], latency_ms: int) -> dict[str, Any]:
    meta = result.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        result["meta"] = meta
    meta["prompt_version"] = AI_REVIEWER_PROMPT_VERSION
    meta["generated_latency_ms"] = max(0, int(latency_ms or 0))
    return result


def _normalize_success_output(
    raw_output: dict[str, Any],
    ai_cfg: dict[str, Any],
    role_profile: dict[str, Any],
    prompt_preview: str,
    source: str,
    reason: str,
    request_id: str = "",
) -> dict[str, Any]:
    if not isinstance(raw_output, dict):
        raise ValueError("AI reviewer response is not an object")

    review_summary = raw_output.get("review_summary")
    if not isinstance(review_summary, str):
        raise ValueError("review_summary must be a string")

    evidence_updates_raw = raw_output.get("evidence_updates")
    if not isinstance(evidence_updates_raw, list):
        raise ValueError("evidence_updates must be a list")
    evidence_updates: list[dict[str, str]] = []
    for item in evidence_updates_raw:
        if not isinstance(item, dict):
            raise ValueError("evidence_updates item must be an object")
        source_name = item.get("source")
        text = item.get("text")
        if not isinstance(source_name, str) or not isinstance(text, str):
            raise ValueError("evidence_updates item requires string source/text")
        note = item.get("note", "")
        if note is not None and not isinstance(note, str):
            raise ValueError("evidence_updates.note must be a string")
        evidence_updates.append({"source": source_name, "text": text, "note": str(note or "")})

    timeline_updates_raw = raw_output.get("timeline_updates")
    if not isinstance(timeline_updates_raw, list):
        raise ValueError("timeline_updates must be a list")
    timeline_updates: list[dict[str, str]] = []
    for item in timeline_updates_raw:
        if not isinstance(item, dict):
            raise ValueError("timeline_updates item must be an object")
        label = item.get("label")
        value = item.get("value")
        if not isinstance(label, str) or not isinstance(value, str):
            raise ValueError("timeline_updates item requires string label/value")
        note = item.get("note", "")
        if note is not None and not isinstance(note, str):
            raise ValueError("timeline_updates.note must be a string")
        timeline_updates.append({"label": label, "value": value, "note": str(note or "")})

    limit_cfg = ai_cfg.get("score_adjustment_limit") or {}
    default_max_delta = int(limit_cfg.get("max_delta_per_dimension", 1) or 1)
    score_adjustments_raw = raw_output.get("score_adjustments")
    if not isinstance(score_adjustments_raw, list):
        raise ValueError("score_adjustments must be a list")
    score_adjustments: list[dict[str, Any]] = []
    for item in score_adjustments_raw:
        if not isinstance(item, dict):
            raise ValueError("score_adjustments item must be an object")
        dimension = item.get("dimension")
        suggested_delta = item.get("suggested_delta")
        reason_text = item.get("reason")
        if not isinstance(dimension, str) or not isinstance(suggested_delta, int) or not isinstance(reason_text, str):
            raise ValueError("score_adjustments item requires dimension/suggested_delta/reason")
        max_delta = item.get("max_delta", default_max_delta)
        if not isinstance(max_delta, int):
            raise ValueError("score_adjustments.max_delta must be an integer")
        score_adjustments.append(
            {
                "dimension": dimension,
                "suggested_delta": suggested_delta,
                "max_delta": max_delta,
                "reason": reason_text,
            }
        )

    risk_adjustment_raw = raw_output.get("risk_adjustment")
    if not isinstance(risk_adjustment_raw, dict):
        raise ValueError("risk_adjustment must be an object")
    risk_adjustment: dict[str, str] = {}
    if risk_adjustment_raw:
        risk_level = risk_adjustment_raw.get("suggested_risk_level")
        if not isinstance(risk_level, str) or risk_level not in ALLOWED_RISK_LEVELS:
            raise ValueError("risk_adjustment.suggested_risk_level is invalid")
        risk_reason = risk_adjustment_raw.get("reason", "")
        if risk_reason is not None and not isinstance(risk_reason, str):
            raise ValueError("risk_adjustment.reason must be a string")
        risk_adjustment = {
            "suggested_risk_level": risk_level,
            "reason": str(risk_reason or ""),
        }

    recommended_action = raw_output.get("recommended_action")
    if not isinstance(recommended_action, str) or recommended_action not in ALLOWED_ACTIONS:
        raise ValueError("recommended_action is invalid")

    return {
        "enabled": True,
        "mode": ai_cfg.get("ai_reviewer_mode", "suggest_only"),
        "review_summary": review_summary,
        "evidence_updates": evidence_updates,
        "timeline_updates": timeline_updates,
        "score_adjustments": score_adjustments,
        "risk_adjustment": risk_adjustment,
        "recommended_action": recommended_action,
        "meta": {
            "source": source,
            "reason": reason,
            "provider": ai_cfg.get("provider"),
            "model": ai_cfg.get("model"),
            "role_template": role_profile.get("profile_name", "通用岗位模板"),
            "allow_break_hard_thresholds": bool(limit_cfg.get("allow_break_hard_thresholds", False)),
            "allow_direct_recommendation_change": bool(
                limit_cfg.get("allow_direct_recommendation_change", False)
            ),
            "prompt_preview": prompt_preview,
            "schema": get_ai_reviewer_output_schema(ai_cfg.get("ai_reviewer_mode", "suggest_only")),
            "request_id": request_id,
        },
    }


def _call_openai_reviewer_api(
    ai_cfg: dict[str, Any],
    prompt_preview: str,
) -> tuple[dict[str, Any], str]:
    api_key_env_name = str(ai_cfg.get("api_key_env_name") or "OPENAI_API_KEY").strip() or "OPENAI_API_KEY"
    api_key = os.getenv(api_key_env_name, "").strip()
    if not api_key:
        raise RuntimeError(f"missing api key env: {api_key_env_name}")

    endpoint = _build_openai_chat_url(str(ai_cfg.get("api_base") or ""))
    body = {
        "model": ai_cfg.get("model") or "gpt-4o-mini",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an AI reviewer in a hiring workflow. "
                    "Rules remain primary. "
                    "Return only strict JSON that matches the provided schema."
                ),
            },
            {"role": "user", "content": prompt_preview},
        ],
        "temperature": 0,
        "response_format": _response_json_schema(str(ai_cfg.get("ai_reviewer_mode") or "suggest_only")),
    }

    request_payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(
        endpoint,
        data=request_payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urlrequest.urlopen(req, timeout=25) as response:
            raw_response = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"http {exc.code}: {payload[:240]}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("timeout") from exc

    try:
        parsed_response = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise RuntimeError("api response is not valid json") from exc

    choice = ((parsed_response.get("choices") or [{}])[0]) or {}
    message = choice.get("message") or {}
    refusal = message.get("refusal")
    if refusal:
        raise RuntimeError(f"model refused: {refusal}")

    content = _extract_message_text(message.get("content"))
    content = _strip_json_wrappers(content)
    if not content:
        raise RuntimeError("empty model content")

    try:
        output = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("model content is not valid json") from exc

    return output, str(parsed_response.get("id") or "")


def _build_stub_ai_review_output(
    parsed_resume: dict[str, Any],
    role_profile: dict[str, Any],
    scoring_config: dict[str, Any] | None,
    screening_result: dict[str, Any],
    score_details: dict[str, Any],
    risk_result: dict[str, Any],
    evidence_snippets: list[dict[str, Any]] | None,
    prompt_preview: str,
    reason: str,
) -> dict[str, Any]:
    ai_cfg = _normalize_ai_reviewer_config(scoring_config)
    caps = ai_cfg.get("capabilities") or {}
    limit_cfg = ai_cfg.get("score_adjustment_limit") or {}

    name = str(parsed_resume.get("name") or "候选人").strip() or "候选人"
    result_text = str(screening_result.get("screening_result") or "")
    risk_level = str(risk_result.get("risk_level") or "unknown").lower()

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
            source_name = str(item.get("source") or "未知来源")
            text = str(item.get("text") or "").strip()
            if text:
                evidence_updates.append({"source": source_name, "text": text, "note": "AI建议补充引用"})

    timeline_updates: list[dict[str, str]] = []
    if caps.get("organize_timeline", False):
        graduation_date = str(parsed_resume.get("graduation_date") or "").strip()
        if graduation_date:
            timeline_updates.append({"label": "毕业时间", "value": graduation_date, "note": ""})

    score_adjustments: list[dict[str, Any]] = []
    if caps.get("suggest_score_adjustment", False):
        max_delta = int(limit_cfg.get("max_delta_per_dimension", 1) or 1)
        expression_score = (score_details.get("表达完整度") or {}).get("score")
        try:
            expression_score_num = int(expression_score or 0)
        except (TypeError, ValueError):
            expression_score_num = 0
        if expression_score_num and expression_score_num <= 3 and evidence_updates:
            score_adjustments.append(
                {
                    "dimension": "表达完整度",
                    "suggested_delta": min(1, max_delta),
                    "max_delta": max_delta,
                    "reason": "检测到可补充证据片段，建议人工确认后小幅上调表达完整度。",
                }
            )

    risk_adjustment: dict[str, str] = {}
    if caps.get("suggest_risk_adjustment", False):
        risk_adjustment = {
            "suggested_risk_level": risk_level if risk_level in ALLOWED_RISK_LEVELS else "unknown",
            "reason": "当前为 stub fallback，默认保持规则风控判断，仅提供结构化占位建议。",
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
            "reason": reason,
            "provider": ai_cfg.get("provider"),
            "model": ai_cfg.get("model"),
            "role_template": role_profile.get("profile_name", "通用岗位模板"),
            "allow_break_hard_thresholds": bool(limit_cfg.get("allow_break_hard_thresholds", False)),
            "allow_direct_recommendation_change": bool(
                limit_cfg.get("allow_direct_recommendation_change", False)
            ),
            "prompt_preview": prompt_preview,
            "schema": get_ai_reviewer_output_schema(ai_cfg.get("ai_reviewer_mode", "suggest_only")),
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
    """Generate structured AI reviewer suggestions."""
    ai_cfg = _normalize_ai_reviewer_config(scoring_config)
    if not ai_cfg.get("enable_ai_reviewer") or ai_cfg.get("ai_reviewer_mode") == "off":
        return _attach_runtime_meta(_empty_ai_review_output("AI reviewer not enabled"), 0)

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

    provider = str(ai_cfg.get("provider") or "openai").strip().lower()
    started_at = perf_counter()
    if provider == "openai":
        try:
            raw_output, request_id = _call_openai_reviewer_api(ai_cfg, prompt_preview)
            return _attach_runtime_meta(
                _normalize_success_output(
                    raw_output=raw_output,
                    ai_cfg=ai_cfg,
                    role_profile=role_profile,
                    prompt_preview=prompt_preview,
                    source="api",
                    reason="openai chat completions structured output",
                    request_id=request_id,
                ),
                int(round((perf_counter() - started_at) * 1000)),
            )
        except Exception as exc:  # noqa: BLE001
            fallback_reason = f"api fallback: {exc}"
    else:
        fallback_reason = f"provider {provider} not implemented, using stub fallback"

    return _attach_runtime_meta(
        _build_stub_ai_review_output(
            parsed_resume=parsed_resume,
            role_profile=role_profile,
            scoring_config=scoring_config,
            screening_result=screening_result,
            score_details=score_details,
            risk_result=risk_result,
            evidence_snippets=evidence_snippets,
            prompt_preview=prompt_preview,
            reason=fallback_reason,
        ),
        int(round((perf_counter() - started_at) * 1000)),
    )
