"""AI secondary review layer with real API call + stub fallback."""

from __future__ import annotations

import json
import os
import re
from time import perf_counter
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

OPENAI_DEFAULT_API_BASE = "https://api.openai.com/v1"
DEEPSEEK_DEFAULT_API_BASE = "https://api.deepseek.com/v1"
AI_REVIEWER_PROMPT_VERSION = "v1"
AI_RULE_SUGGESTER_PROMPT_VERSION = "v1"
ALLOWED_RISK_LEVELS = {"low", "medium", "high", "unknown"}
ALLOWED_ACTIONS = {"proceed", "manual_review", "hold", "no_action"}
AI_PROVIDER_OPTIONS = ["openai", "openai_compatible", "deepseek", "azure_openai", "anthropic", "mock"]
OPENAI_COMPATIBLE_PROVIDERS = {"openai", "openai_compatible", "deepseek"}
OPENAI_JSON_SCHEMA_PROVIDERS = {"openai"}
OPENAI_JSON_OBJECT_PROVIDERS = {"deepseek"}
API_BASE_REQUIRED_PROVIDERS = {"openai_compatible"}
MOCK_PROVIDERS = {"mock"}
AI_API_KEY_MODES = {"direct_input", "env_name"}
ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_LATEST_AI_CALL_STATUS: dict[str, Any] = {}
AI_PROVIDER_DEFAULTS = {
    "openai": {
        "model": "gpt-4o-mini",
        "api_base": OPENAI_DEFAULT_API_BASE,
        "api_key_env_name": "OPENAI_API_KEY",
    },
    "openai_compatible": {
        "model": "gpt-4o-mini",
        "api_base": "",
        "api_key_env_name": "OPENAI_API_KEY",
    },
    "deepseek": {
        "model": "deepseek-chat",
        "api_base": DEEPSEEK_DEFAULT_API_BASE,
        "api_key_env_name": "DEEPSEEK_API_KEY",
    },
    "azure_openai": {
        "model": "gpt-4o-mini",
        "api_base": "",
        "api_key_env_name": "AZURE_OPENAI_API_KEY",
    },
    "anthropic": {
        "model": "claude-3-5-sonnet-latest",
        "api_base": "",
        "api_key_env_name": "ANTHROPIC_API_KEY",
    },
    "mock": {
        "model": "mock",
        "api_base": "",
        "api_key_env_name": "",
    },
}


def _record_latest_ai_call_status(
    *,
    purpose: str,
    runtime_cfg: dict[str, Any],
    source: str,
    success: bool,
    reason: str = "",
    request_id: str = "",
) -> None:
    global _LATEST_AI_CALL_STATUS
    key_details = _resolve_api_key_details(runtime_cfg)
    api_key_env_name = str(key_details.get("env_name") or "")
    _LATEST_AI_CALL_STATUS = {
        "timestamp": int(round(perf_counter() * 1000)),
        "purpose": str(purpose or "generic"),
        "provider": str(runtime_cfg.get("provider") or ""),
        "model": str(runtime_cfg.get("model") or ""),
        "api_base": str(runtime_cfg.get("api_base") or ""),
        "api_key_env_name": api_key_env_name,
        "api_key_mode": str(key_details.get("mode") or "env_name"),
        "api_key_mode_label": str(key_details.get("mode_label") or ""),
        "api_key_present": bool(key_details.get("key_value_present")),
        "api_key_env_detected": bool(key_details.get("env_value_present")),
        "source": str(source or ""),
        "success": bool(success),
        "reason": str(reason or ""),
        "failure_reason": "" if success else str(reason or ""),
        "request_id": str(request_id or ""),
    }


def get_latest_ai_call_status() -> dict[str, Any]:
    return dict(_LATEST_AI_CALL_STATUS)
AI_MODEL_PRESETS = {
    "openai": [
        {"label": "GPT-4o Mini", "value": "gpt-4o-mini"},
        {"label": "GPT-4o", "value": "gpt-4o"},
        {"label": "GPT-4.1 Mini", "value": "gpt-4.1-mini"},
        {"label": "GPT-4.1", "value": "gpt-4.1"},
    ],
    "openai_compatible": [
        {"label": "OpenAI GPT-4o Mini", "value": "gpt-4o-mini"},
        {"label": "OpenAI GPT-4o", "value": "gpt-4o"},
        {"label": "OpenAI GPT-4.1 Mini", "value": "gpt-4.1-mini"},
        {"label": "OpenAI GPT-4.1", "value": "gpt-4.1"},
        {"label": "DeepSeek-V3.2 Chat (deepseek-chat)", "value": "deepseek-chat"},
        {"label": "DeepSeek-V3.2 Reasoner (deepseek-reasoner)", "value": "deepseek-reasoner"},
    ],
    "deepseek": [
        {"label": "DeepSeek-V3.2 Chat (deepseek-chat)", "value": "deepseek-chat"},
        {"label": "DeepSeek-V3.2 Reasoner (deepseek-reasoner)", "value": "deepseek-reasoner"},
    ],
    "azure_openai": [
        {"label": "GPT-4o Mini", "value": "gpt-4o-mini"},
        {"label": "GPT-4o", "value": "gpt-4o"},
        {"label": "GPT-4.1 Mini", "value": "gpt-4.1-mini"},
    ],
    "anthropic": [
        {"label": "Claude 3.5 Sonnet", "value": "claude-3-5-sonnet-latest"},
    ],
    "mock": [
        {"label": "Mock", "value": "mock"},
    ],
}


def get_ai_reviewer_prompt_version() -> str:
    return AI_REVIEWER_PROMPT_VERSION


def get_ai_rule_suggester_prompt_version() -> str:
    return AI_RULE_SUGGESTER_PROMPT_VERSION


def get_ai_provider_options() -> list[str]:
    return list(AI_PROVIDER_OPTIONS)


def get_ai_model_presets(provider: str) -> list[dict[str, str]]:
    provider_norm = str(provider or "openai").strip().lower()
    presets = AI_MODEL_PRESETS.get(provider_norm) or AI_MODEL_PRESETS["openai"]
    return [dict(item) for item in presets]


def get_default_ai_model(provider: str) -> str:
    provider_norm = str(provider or "openai").strip().lower()
    return str((AI_PROVIDER_DEFAULTS.get(provider_norm) or AI_PROVIDER_DEFAULTS["openai"]).get("model") or "")


def get_default_ai_api_base(provider: str) -> str:
    provider_norm = str(provider or "openai").strip().lower()
    return str((AI_PROVIDER_DEFAULTS.get(provider_norm) or AI_PROVIDER_DEFAULTS["openai"]).get("api_base") or "")


def get_default_ai_api_key_env_name(provider: str) -> str:
    provider_norm = str(provider or "openai").strip().lower()
    return str((AI_PROVIDER_DEFAULTS.get(provider_norm) or AI_PROVIDER_DEFAULTS["openai"]).get("api_key_env_name") or "")


def provider_requires_explicit_api_base(provider: str) -> bool:
    provider_norm = str(provider or "").strip().lower()
    return provider_norm in API_BASE_REQUIRED_PROVIDERS


def resolve_ai_api_base(provider: str, configured_api_base: str = "") -> str:
    clean = str(configured_api_base or "").strip()
    return clean or get_default_ai_api_base(provider)


def resolve_ai_api_key_env_name(provider: str, configured_env_name: str = "") -> str:
    clean = str(configured_env_name or "").strip()
    return clean or get_default_ai_api_key_env_name(provider)


def _is_valid_env_name(value: str) -> bool:
    clean = str(value or "").strip()
    return bool(clean) and bool(ENV_NAME_PATTERN.fullmatch(clean))


def _looks_like_api_key(value: str) -> bool:
    clean = str(value or "").strip()
    lower = clean.lower()
    if not clean:
        return False
    if lower.startswith(("sk-", "sk_proj_", "sk-proj-", "dsk_", "dsk-", "bearer ")):
        return True
    if _is_valid_env_name(clean):
        return False
    return len(clean) >= 24 and any(ch.isalpha() for ch in clean) and any(ch.isdigit() for ch in clean)


def _resolve_api_key_details(runtime_cfg: dict[str, Any]) -> dict[str, Any]:
    provider = str(runtime_cfg.get("provider") or "openai").strip().lower() or "openai"
    direct_value = str(runtime_cfg.get("api_key_value") or "").strip()
    raw_mode = str(runtime_cfg.get("api_key_mode") or "").strip().lower()
    mode = raw_mode if raw_mode in AI_API_KEY_MODES else ("direct_input" if direct_value else "env_name")
    api_key_env_name = resolve_ai_api_key_env_name(provider, str(runtime_cfg.get("api_key_env_name") or ""))
    env_value = os.getenv(api_key_env_name, "").strip() if api_key_env_name else ""
    using_direct = mode == "direct_input"
    key_value = direct_value if using_direct else env_value
    return {
        "mode": mode,
        "mode_label": "直接输入 API Key" if using_direct else "环境变量名",
        "direct_value_present": bool(direct_value),
        "env_name": api_key_env_name,
        "env_value_present": bool(env_value),
        "env_name_looks_like_key": _looks_like_api_key(api_key_env_name),
        "key_value": key_value,
        "key_value_present": bool(key_value),
    }


def _missing_api_key_reason(runtime_cfg: dict[str, Any], key_details: dict[str, Any]) -> str:
    provider = str(runtime_cfg.get("provider") or "openai").strip().lower() or "openai"
    if key_details.get("mode") == "direct_input":
        return "API key 缺失：当前使用“直接输入 API Key”模式，请粘贴真实 key。"

    env_name = str(key_details.get("env_name") or "").strip()
    if key_details.get("env_name_looks_like_key"):
        return (
            "环境变量名看起来填成了真实 API key。请切换到“直接输入 API Key”模式，"
            f"或在这里填写例如 {get_default_ai_api_key_env_name(provider) or 'DEEPSEEK_API_KEY'}。"
        )
    if not env_name:
        return f"API key 缺失：请填写环境变量名，例如 {get_default_ai_api_key_env_name(provider)}。"
    return f"未检测到环境变量 {env_name}。"


def _friendly_connection_error(exc: Exception, runtime_cfg: dict[str, Any], key_details: dict[str, Any] | None = None) -> str:
    message = str(exc or "").strip()
    lower = message.lower()
    provider = str(runtime_cfg.get("provider") or "openai").strip().lower() or "openai"
    api_base = str(runtime_cfg.get("api_base") or "").strip()
    details = key_details if isinstance(key_details, dict) else _resolve_api_key_details(runtime_cfg)

    if "missing api key" in lower or "未检测到环境变量" in message or "环境变量名看起来填成了真实" in message:
        return _missing_api_key_reason(runtime_cfg, details)
    if "missing api_base" in lower:
        example_base = get_default_ai_api_base(provider) or "https://api.deepseek.com/v1"
        return f"api_base 缺失或未填写。请检查地址，常见示例：{example_base}"
    if "timeout" in lower or "timed out" in lower:
        return "网络超时，请检查网络、代理或稍后重试。"
    if lower.startswith("http 400"):
        if "model" in lower and any(token in lower for token in ["not found", "does not exist", "unknown"]):
            return "model 不存在或当前账号无权访问，请检查 model 名称。"
        return "请求被上游拒绝，常见原因：api_base 错误、model 名称不正确，或当前 provider 不支持该请求格式。"
    if lower.startswith("http 401"):
        return "API key 无效或未授权，请检查 key 是否正确。"
    if lower.startswith("http 403"):
        return "API key 没有访问当前 provider / model 的权限。"
    if lower.startswith("http 404"):
        return f"api_base 可能错误，未找到对应接口：{api_base or '-'}"
    if lower.startswith("http 429"):
        return "请求过于频繁，或当前 API key / 账户额度不足。"
    if lower.startswith("http 5"):
        return "上游 AI 服务暂时不可用，请稍后重试。"
    if any(token in lower for token in ["getaddrinfo failed", "name or service not known", "nodename nor servname", "no address associated"]):
        return f"无法连接到 api_base，请检查地址是否正确：{api_base or '-'}"
    if "connection refused" in lower:
        return f"无法连接到 api_base，对端拒绝连接：{api_base or '-'}"
    if lower.startswith("network error:"):
        return f"网络连接失败，请检查 api_base、网络或代理配置：{api_base or '-'}"
    if "certificate" in lower or "ssl" in lower:
        return "TLS / SSL 握手失败，请检查 api_base、证书或代理设置。"
    if any(token in lower for token in ["invalid api key", "incorrect api key", "authentication", "unauthorized"]):
        return "API key 无效或未授权，请检查 key 是否正确。"
    if any(token in lower for token in ["model_not_found", "unknown model", "does not exist"]):
        return "model 不存在或当前账号无权访问，请检查 model 名称。"
    return message or "请求失败，请检查 provider、api_base 和 API key 配置。"


def resolve_ai_runtime_config(ai_cfg: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(ai_cfg or {})
    provider = str(cfg.get("provider") or "openai").strip().lower() or "openai"
    model = str(cfg.get("model") or "").strip() or get_default_ai_model(provider)
    api_base = resolve_ai_api_base(provider, str(cfg.get("api_base") or ""))
    api_key_env_name = resolve_ai_api_key_env_name(provider, str(cfg.get("api_key_env_name") or ""))
    api_key_value = str(cfg.get("api_key_value") or "")
    raw_mode = str(cfg.get("api_key_mode") or "").strip().lower()
    api_key_mode = raw_mode if raw_mode in AI_API_KEY_MODES else ("direct_input" if api_key_value.strip() else "env_name")
    return {
        **cfg,
        "provider": provider,
        "model": model,
        "api_base": api_base,
        "api_key_env_name": api_key_env_name,
        "api_key_mode": api_key_mode,
        "api_key_value": api_key_value,
    }


def _provider_supports_json_schema(provider: str) -> bool:
    provider_norm = str(provider or "").strip().lower()
    return provider_norm in OPENAI_JSON_SCHEMA_PROVIDERS


def _provider_supports_json_object(provider: str) -> bool:
    provider_norm = str(provider or "").strip().lower()
    return provider_norm in OPENAI_JSON_OBJECT_PROVIDERS


def _resolve_openai_compatible_endpoint(runtime_cfg: dict[str, Any]) -> str:
    provider = str(runtime_cfg.get("provider") or "openai").strip().lower()
    api_base = str(runtime_cfg.get("api_base") or "").strip()
    if provider_requires_explicit_api_base(provider) and not api_base:
        raise RuntimeError(f"missing api_base for provider {provider}")
    return _build_openai_chat_url(api_base or get_default_ai_api_base(provider))


def _stub_reason_for_provider(provider: str) -> str:
    provider_norm = str(provider or "").strip().lower()
    if provider_norm in MOCK_PROVIDERS:
        return "mock provider selected, using structured stub fallback"
    return f"provider {provider_norm or '-'} not implemented, using stub fallback"


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
    return _json_schema_response_format("hiremate_ai_reviewer_output", schema)


def _json_schema_response_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
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


def _scoring_threshold_keys() -> list[str]:
    return ["pass_line", "review_line", "min_experience", "min_skill", "min_expression"]


def _build_ai_rule_suggester_schema(profile_name: str, current_cfg: dict[str, Any]) -> dict[str, Any]:
    weights = (current_cfg.get("weights") or {}) if isinstance(current_cfg, dict) else {}
    hard_thresholds = (current_cfg.get("hard_thresholds") or current_cfg.get("hard_flags") or {}) if isinstance(current_cfg, dict) else {}
    weight_keys = [str(key) for key in weights.keys()] or [
        "教育背景匹配度",
        "相关经历匹配度",
        "技能匹配度",
        "表达完整度",
    ]
    hard_keys = [str(key) for key in hard_thresholds.keys()]

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["role_template", "weights", "hard_thresholds", "screening_thresholds", "risk_focus", "notes"],
        "properties": {
            "role_template": {"type": "string"},
            "weights": {
                "type": "object",
                "additionalProperties": False,
                "required": weight_keys,
                "properties": {key: {"type": "number"} for key in weight_keys},
            },
            "hard_thresholds": {
                "type": "object",
                "additionalProperties": False,
                "required": hard_keys,
                "properties": {key: {"type": "boolean"} for key in hard_keys},
            },
            "screening_thresholds": {
                "type": "object",
                "additionalProperties": False,
                "required": _scoring_threshold_keys(),
                "properties": {key: {"type": "integer"} for key in _scoring_threshold_keys()},
            },
            "risk_focus": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
    }


def build_ai_rule_suggester_prompt(profile_name: str, current_cfg: dict[str, Any], jd_text: str) -> str:
    schema = _build_ai_rule_suggester_schema(profile_name, current_cfg)
    payload = {
        "role_template": profile_name,
        "current_scoring_config": current_cfg,
        "jd_text": jd_text,
    }
    return (
        "你是招聘评分规则优化助手，但规则评分器仍然是主评分器。\n"
        "请基于当前岗位 JD 与现有评分配置，给出更适合该岗位的评分细则建议。\n"
        "只允许输出结构化 JSON，不要输出额外解释。\n"
        "要求：\n"
        "1. weights 保持 0-1 之间的数值；\n"
        "2. screening_thresholds 保持 1-5 的整数；\n"
        "3. hard_thresholds 只能输出布尔值；\n"
        "4. notes 用简短中文说明建议依据；\n"
        "5. 不要发明 JD 中不存在的硬性要求。\n"
        f"目标 schema：\n{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        f"输入：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _normalize_ai_rule_suggestion_output(
    raw_output: dict[str, Any],
    profile_name: str,
    current_cfg: dict[str, Any],
    ai_cfg: dict[str, Any],
    *,
    source: str,
    reason: str,
    prompt_preview: str,
    request_id: str = "",
) -> dict[str, Any]:
    defaults = current_cfg if isinstance(current_cfg, dict) else {}
    weights_default = dict(defaults.get("weights") or {})
    hard_default = dict(defaults.get("hard_thresholds") or defaults.get("hard_flags") or {})
    threshold_default = dict(defaults.get("screening_thresholds") or defaults.get("thresholds") or {})
    risk_focus_default = list(defaults.get("risk_focus") or [])

    if not isinstance(raw_output, dict):
        raise ValueError("AI rule suggester response is not an object")

    role_template = str(raw_output.get("role_template") or profile_name).strip() or profile_name

    weights_raw = raw_output.get("weights")
    if not isinstance(weights_raw, dict):
        raise ValueError("weights must be an object")
    weights = {}
    for key, default_value in weights_default.items():
        try:
            weights[key] = float(weights_raw.get(key, default_value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"weights.{key} must be numeric") from exc

    hard_raw = raw_output.get("hard_thresholds")
    if not isinstance(hard_raw, dict):
        raise ValueError("hard_thresholds must be an object")
    hard_thresholds = {key: bool(hard_raw.get(key, default_value)) for key, default_value in hard_default.items()}

    thresholds_raw = raw_output.get("screening_thresholds")
    if not isinstance(thresholds_raw, dict):
        raise ValueError("screening_thresholds must be an object")
    screening_thresholds = {}
    for key in _scoring_threshold_keys():
        try:
            screening_thresholds[key] = int(thresholds_raw.get(key, threshold_default.get(key, 0)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"screening_thresholds.{key} must be an integer") from exc

    risk_focus_raw = raw_output.get("risk_focus")
    if not isinstance(risk_focus_raw, list):
        raise ValueError("risk_focus must be a list")
    risk_focus = [str(item).strip() for item in risk_focus_raw if str(item).strip()] or risk_focus_default

    notes_raw = raw_output.get("notes")
    if not isinstance(notes_raw, list):
        raise ValueError("notes must be a list")
    notes = [str(item).strip() for item in notes_raw if str(item).strip()]

    return {
        "role_template": role_template,
        "weights": weights,
        "hard_thresholds": hard_thresholds,
        "screening_thresholds": screening_thresholds,
        "risk_focus": risk_focus,
        "notes": notes,
        "meta": {
            "source": source,
            "reason": reason,
            "provider": ai_cfg.get("provider"),
            "model": ai_cfg.get("model"),
            "api_base": ai_cfg.get("api_base"),
            "api_key_env_name": ai_cfg.get("api_key_env_name"),
            "prompt_version": AI_RULE_SUGGESTER_PROMPT_VERSION,
            "prompt_preview": prompt_preview,
            "request_id": request_id,
        },
    }


def _build_stub_ai_rule_suggestion(
    profile_name: str,
    current_cfg: dict[str, Any],
    jd_text: str,
    ai_cfg: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    keywords = [key for key in ["SQL", "Python", "A/B", "访谈", "RAG", "Prompt"] if key.lower() in (jd_text or "").lower()]
    return {
        "role_template": profile_name,
        "weights": dict(current_cfg.get("weights") or {}),
        "hard_thresholds": dict(current_cfg.get("hard_thresholds") or current_cfg.get("hard_flags") or {}),
        "screening_thresholds": dict(current_cfg.get("screening_thresholds") or current_cfg.get("thresholds") or {}),
        "risk_focus": list(current_cfg.get("risk_focus") or []),
        "notes": [
            "当前返回为 stub fallback，默认保留现有评分细则结构。",
            "真实 API 可用后，可基于岗位 JD 生成更具体的权重和门槛建议。",
            f"JD 关键词命中：{keywords if keywords else '无明显额外信号'}",
        ],
        "meta": {
            "source": "stub",
            "reason": reason,
            "provider": ai_cfg.get("provider"),
            "model": ai_cfg.get("model"),
            "api_base": ai_cfg.get("api_base"),
            "api_key_env_name": ai_cfg.get("api_key_env_name"),
            "prompt_version": AI_RULE_SUGGESTER_PROMPT_VERSION,
        },
    }


def run_ai_rule_suggester(profile_name: str, current_cfg: dict[str, Any], jd_text: str, ai_cfg: dict[str, Any]) -> dict[str, Any]:
    runtime_cfg = resolve_ai_runtime_config(ai_cfg)
    if not runtime_cfg.get("enable_ai_rule_suggester"):
        output = _attach_runtime_meta(
            _build_stub_ai_rule_suggestion(
                profile_name,
                current_cfg,
                jd_text,
                runtime_cfg,
                reason="AI rule suggester not enabled",
            ),
            0,
            prompt_version=AI_RULE_SUGGESTER_PROMPT_VERSION,
        )
        _record_latest_ai_call_status(
            purpose="ai_rule_suggester",
            runtime_cfg=runtime_cfg,
            source="stub",
            success=False,
            reason="AI rule suggester not enabled",
        )
        return output

    prompt_preview = build_ai_rule_suggester_prompt(profile_name, current_cfg, jd_text)
    provider = str(runtime_cfg.get("provider") or "openai").strip().lower()
    started_at = perf_counter()

    if provider in MOCK_PROVIDERS:
        fallback_reason = _stub_reason_for_provider(provider)
    elif provider in OPENAI_COMPATIBLE_PROVIDERS:
        try:
            raw_output, request_id = _call_openai_compatible_json_api(
                runtime_cfg,
                system_prompt=(
                    "You optimize structured hiring scoring rules. "
                    "Return only JSON that matches the requested schema."
                ),
                user_prompt=prompt_preview,
                schema_name="hiremate_ai_rule_suggester_output",
                json_schema=_build_ai_rule_suggester_schema(profile_name, current_cfg),
                prefer_structured_output=_provider_supports_json_schema(provider),
            )
            output = _attach_runtime_meta(
                _normalize_ai_rule_suggestion_output(
                    raw_output,
                    profile_name,
                    current_cfg,
                    runtime_cfg,
                    source="api",
                    reason=f"{provider} chat completions json output",
                    prompt_preview=prompt_preview,
                    request_id=request_id,
                ),
                int(round((perf_counter() - started_at) * 1000)),
                prompt_version=AI_RULE_SUGGESTER_PROMPT_VERSION,
            )
            _record_latest_ai_call_status(
                purpose="ai_rule_suggester",
                runtime_cfg=runtime_cfg,
                source="api",
                success=True,
                reason=f"{provider} chat completions json output",
                request_id=request_id,
            )
            return output
        except Exception as exc:  # noqa: BLE001
            fallback_reason = f"api fallback: {exc}"
    else:
        fallback_reason = _stub_reason_for_provider(provider)

    output = _attach_runtime_meta(
        _build_stub_ai_rule_suggestion(
            profile_name,
            current_cfg,
            jd_text,
            runtime_cfg,
            reason=fallback_reason,
        ),
        int(round((perf_counter() - started_at) * 1000)),
        prompt_version=AI_RULE_SUGGESTER_PROMPT_VERSION,
    )
    _record_latest_ai_call_status(
        purpose="ai_rule_suggester",
        runtime_cfg=runtime_cfg,
        source="stub",
        success=False,
        reason=fallback_reason,
    )
    return output


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


def _parse_json_object_content(payload: str) -> dict[str, Any]:
    text = _strip_json_wrappers(payload)
    if not text:
        raise RuntimeError("empty model content")

    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1].strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(parsed, dict):
            raise RuntimeError("model content is not a json object")
        return parsed

    raise RuntimeError("model content is not valid json") from last_error


def _call_openai_compatible_json_api(
    ai_cfg: dict[str, Any],
    *,
    system_prompt: str,
    user_prompt: str,
    schema_name: str,
    json_schema: dict[str, Any] | None = None,
    prefer_structured_output: bool = False,
) -> tuple[dict[str, Any], str]:
    runtime_cfg = resolve_ai_runtime_config(ai_cfg)
    provider = str(runtime_cfg.get("provider") or "openai")
    if provider not in OPENAI_COMPATIBLE_PROVIDERS:
        raise RuntimeError(f"provider {provider} not implemented")

    endpoint = _resolve_openai_compatible_endpoint(runtime_cfg)
    key_details = _resolve_api_key_details(runtime_cfg)
    api_key = str(key_details.get("key_value") or "").strip()
    if not api_key:
        raise RuntimeError(_missing_api_key_reason(runtime_cfg, key_details))

    body: dict[str, Any] = {
        "model": runtime_cfg.get("model") or get_default_ai_model(provider),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
    }
    if json_schema and prefer_structured_output and _provider_supports_json_schema(provider):
        body["response_format"] = _json_schema_response_format(schema_name, json_schema)
    elif json_schema and _provider_supports_json_object(provider):
        body["response_format"] = {"type": "json_object"}

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
    output = _parse_json_object_content(content)
    return output, str(parsed_response.get("id") or "")


def _attach_runtime_meta(result: dict[str, Any], latency_ms: int, *, prompt_version: str = AI_REVIEWER_PROMPT_VERSION) -> dict[str, Any]:
    meta = result.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        result["meta"] = meta
    meta["prompt_version"] = prompt_version
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
            "api_base": ai_cfg.get("api_base"),
            "api_key_env_name": ai_cfg.get("api_key_env_name"),
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
    schema = dict(get_ai_reviewer_output_schema(str(ai_cfg.get("ai_reviewer_mode") or "suggest_only")))
    schema.pop("mode_note", None)
    return _call_openai_compatible_json_api(
        ai_cfg,
        system_prompt=(
            "You are an AI reviewer in a hiring workflow. "
            "Rules remain primary. "
            "Return only JSON that matches the provided schema."
        ),
        user_prompt=prompt_preview,
        schema_name="hiremate_ai_reviewer_output",
        json_schema=schema,
        prefer_structured_output=True,
    )


def _build_stub_ai_review_output(
    parsed_resume: dict[str, Any],
    role_profile: dict[str, Any],
    ai_cfg: dict[str, Any],
    screening_result: dict[str, Any],
    score_details: dict[str, Any],
    risk_result: dict[str, Any],
    evidence_snippets: list[dict[str, Any]] | None,
    prompt_preview: str,
    reason: str,
) -> dict[str, Any]:
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
            "api_base": ai_cfg.get("api_base"),
            "api_key_env_name": ai_cfg.get("api_key_env_name"),
            "role_template": role_profile.get("profile_name", "通用岗位模板"),
            "allow_break_hard_thresholds": bool(limit_cfg.get("allow_break_hard_thresholds", False)),
            "allow_direct_recommendation_change": bool(
                limit_cfg.get("allow_direct_recommendation_change", False)
            ),
            "prompt_preview": prompt_preview,
            "schema": get_ai_reviewer_output_schema(ai_cfg.get("ai_reviewer_mode", "suggest_only")),
        },
    }


def test_ai_connection(ai_cfg: dict[str, Any], *, purpose: str = "generic") -> dict[str, Any]:
    runtime_cfg = resolve_ai_runtime_config(ai_cfg)
    provider = str(runtime_cfg.get("provider") or "openai")
    key_details = _resolve_api_key_details(runtime_cfg)
    api_key_env_name = str(key_details.get("env_name") or "")
    result = {
        "provider": provider,
        "model": str(runtime_cfg.get("model") or ""),
        "api_base": str(runtime_cfg.get("api_base") or ""),
        "api_key_env_name": api_key_env_name or "-",
        "api_key_mode": str(key_details.get("mode") or "env_name"),
        "api_key_mode_label": str(key_details.get("mode_label") or ""),
        "api_key_present": bool(key_details.get("key_value_present")),
        "api_key_env_detected": bool(key_details.get("env_value_present")),
        "success": False,
        "reason": "",
        "request_id": "",
        "purpose": purpose,
        "latency_ms": 0,
    }

    if provider in MOCK_PROVIDERS:
        result["success"] = True
        result["reason"] = "mock provider, skipped real network call"
        return result

    if provider not in OPENAI_COMPATIBLE_PROVIDERS:
        result["reason"] = f"provider {provider} not implemented"
        return result

    if provider_requires_explicit_api_base(provider) and not str(runtime_cfg.get("api_base") or "").strip():
        result["reason"] = _friendly_connection_error(RuntimeError(f"missing api_base for provider {provider}"), runtime_cfg, key_details)
        return result

    if not key_details.get("key_value_present"):
        result["reason"] = _missing_api_key_reason(runtime_cfg, key_details)
        return result

    started_at = perf_counter()
    try:
        output, request_id = _call_openai_compatible_json_api(
            runtime_cfg,
            system_prompt="Return only JSON.",
            user_prompt=(
                "Return a JSON object with exactly these fields: "
                '{"status":"ok","message":"connection ok","provider_echo":"<provider>"}'
            ),
            schema_name="hiremate_ai_connection_test",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["status", "message", "provider_echo"],
                "properties": {
                    "status": {"type": "string"},
                    "message": {"type": "string"},
                    "provider_echo": {"type": "string"},
                },
            },
            prefer_structured_output=_provider_supports_json_schema(provider),
        )
        result["success"] = True
        result["reason"] = str(output.get("message") or "connection ok")
        result["request_id"] = request_id
    except Exception as exc:  # noqa: BLE001
        result["reason"] = _friendly_connection_error(exc, runtime_cfg, key_details)

    result["latency_ms"] = int(round((perf_counter() - started_at) * 1000))
    return result


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
    ai_cfg = resolve_ai_runtime_config(_normalize_ai_reviewer_config(scoring_config))
    if not ai_cfg.get("enable_ai_reviewer") or ai_cfg.get("ai_reviewer_mode") == "off":
        output = _attach_runtime_meta(_empty_ai_review_output("AI reviewer not enabled"), 0)
        _record_latest_ai_call_status(
            purpose="ai_reviewer",
            runtime_cfg=ai_cfg,
            source="stub",
            success=False,
            reason="AI reviewer not enabled",
        )
        return output

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
    if provider in MOCK_PROVIDERS:
        fallback_reason = _stub_reason_for_provider(provider)
    elif provider in OPENAI_COMPATIBLE_PROVIDERS:
        try:
            raw_output, request_id = _call_openai_reviewer_api(ai_cfg, prompt_preview)
            output = _attach_runtime_meta(
                _normalize_success_output(
                    raw_output=raw_output,
                    ai_cfg=ai_cfg,
                    role_profile=role_profile,
                    prompt_preview=prompt_preview,
                    source="api",
                    reason=f"{provider} chat completions json output",
                    request_id=request_id,
                ),
                int(round((perf_counter() - started_at) * 1000)),
            )
            _record_latest_ai_call_status(
                purpose="ai_reviewer",
                runtime_cfg=ai_cfg,
                source="api",
                success=True,
                reason=f"{provider} chat completions json output",
                request_id=request_id,
            )
            return output
        except Exception as exc:  # noqa: BLE001
            fallback_reason = f"api fallback: {exc}"
    else:
        fallback_reason = _stub_reason_for_provider(provider)

    output = _attach_runtime_meta(
        _build_stub_ai_review_output(
            parsed_resume=parsed_resume,
            role_profile=role_profile,
            ai_cfg=ai_cfg,
            screening_result=screening_result,
            score_details=score_details,
            risk_result=risk_result,
            evidence_snippets=evidence_snippets,
            prompt_preview=prompt_preview,
            reason=fallback_reason,
        ),
        int(round((perf_counter() - started_at) * 1000)),
    )
    _record_latest_ai_call_status(
        purpose="ai_reviewer",
        runtime_cfg=ai_cfg,
        source="stub",
        success=False,
        reason=fallback_reason,
    )
    return output
