from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from time import perf_counter
from typing import Any

from src.ai_reviewer import (
    get_ai_reviewer_prompt_version,
    get_default_ai_api_base,
    get_default_ai_api_key_env_name,
    get_default_ai_model,
    run_ai_reviewer,
    test_ai_connection,
)
from src.candidate_store import (
    ROW_KEY_CANDIDATE_POOL,
    ROW_KEY_MANUAL_DECISION,
    ROW_KEY_MANUAL_NOTE,
    ROW_KEY_MANUAL_PRIORITY,
    ROW_KEY_REVIEW_SUMMARY,
    ROW_KEY_RISK_LEVEL,
    ROW_KEY_SCREENING_RESULT,
    can_user_operate_candidate,
    load_batch,
    persist_candidate_snapshot,
)
from src.review_store import upsert_manual_review
from src.role_profiles import BASE_WEIGHT_KEYS, detect_role_profile, get_profile_by_name
from src.scorer import to_score_values
from src.screener import build_evidence_bridge, build_screening_decision


OVERALL_DIMENSION = "综合推荐度"
BASE_DIMENSIONS = list(BASE_WEIGHT_KEYS)
DIMENSION_LABELS = [*BASE_DIMENSIONS, OVERALL_DIMENSION]


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def default_ai_reviewer_config() -> dict[str, Any]:
    return {
        "enable_ai_reviewer": False,
        "ai_reviewer_mode": "suggest_only",
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


def sanitize_runtime_cfg_for_storage(ai_cfg: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(ai_cfg or {})
    payload.pop("api_key_value", None)
    return payload


def normalize_batch_ai_runtime(runtime_cfg: dict[str, Any] | None, *, jd_title: str = "") -> dict[str, Any]:
    defaults = {
        "enable_ai_reviewer": False,
        "ai_reviewer_mode": "suggest_only",
        "provider": "openai",
        "model": get_default_ai_model("openai"),
        "api_base": get_default_ai_api_base("openai"),
        "api_key_mode": "env_name",
        "api_key_env_name": get_default_ai_api_key_env_name("openai"),
        "api_key_value": "",
        "auto_generate_for_new_batch": False,
    }
    incoming = runtime_cfg if isinstance(runtime_cfg, dict) else {}
    provider = str(incoming.get("provider") or defaults["provider"]).strip().lower() or "openai"
    return {
        "enable_ai_reviewer": bool(incoming.get("enable_ai_reviewer", defaults["enable_ai_reviewer"])),
        "ai_reviewer_mode": "suggest_only",
        "provider": provider,
        "model": str(incoming.get("model") or get_default_ai_model(provider)).strip() or get_default_ai_model(provider),
        "api_base": str(incoming.get("api_base") or get_default_ai_api_base(provider)).strip(),
        "api_key_mode": str(incoming.get("api_key_mode") or "env_name").strip().lower() or "env_name",
        "api_key_env_name": str(
            incoming.get("api_key_env_name") or get_default_ai_api_key_env_name(provider)
        ).strip()
        or get_default_ai_api_key_env_name(provider),
        "api_key_value": str(incoming.get("api_key_value") or ""),
        "auto_generate_for_new_batch": bool(incoming.get("auto_generate_for_new_batch", False)),
        "jd_title": str(jd_title or incoming.get("jd_title") or "").strip(),
    }


def apply_batch_ai_runtime_to_detail(
    detail: dict[str, Any],
    runtime_cfg: dict[str, Any] | None,
    *,
    jd_title: str = "",
) -> dict[str, Any]:
    runtime = normalize_batch_ai_runtime(runtime_cfg, jd_title=jd_title)
    safe_runtime = sanitize_runtime_cfg_for_storage(runtime)
    parsed_jd = detail.get("parsed_jd") if isinstance(detail.get("parsed_jd"), dict) else {}
    base_scoring_cfg = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
    reviewer_defaults = (
        base_scoring_cfg.get("ai_reviewer") if isinstance(base_scoring_cfg.get("ai_reviewer"), dict) else {}
    )
    effective_ai_cfg = {
        **default_ai_reviewer_config(),
        **reviewer_defaults,
        "enable_ai_reviewer": bool(runtime.get("enable_ai_reviewer", False)),
        "ai_reviewer_mode": "suggest_only",
        "provider": str(safe_runtime.get("provider") or reviewer_defaults.get("provider") or "openai"),
        "model": str(safe_runtime.get("model") or reviewer_defaults.get("model") or ""),
        "api_base": str(safe_runtime.get("api_base") or reviewer_defaults.get("api_base") or ""),
        "api_key_mode": str(safe_runtime.get("api_key_mode") or reviewer_defaults.get("api_key_mode") or "env_name"),
        "api_key_env_name": str(
            safe_runtime.get("api_key_env_name") or reviewer_defaults.get("api_key_env_name") or ""
        ),
        "capabilities": {
            **default_ai_reviewer_config().get("capabilities", {}),
            **(reviewer_defaults.get("capabilities") or {}),
        },
        "score_adjustment_limit": {
            **default_ai_reviewer_config().get("score_adjustment_limit", {}),
            **(reviewer_defaults.get("score_adjustment_limit") or {}),
        },
    }
    if runtime.get("api_key_value"):
        effective_ai_cfg["api_key_value"] = str(runtime.get("api_key_value") or "")
    parsed_jd["scoring_config"] = {**base_scoring_cfg, "ai_reviewer": effective_ai_cfg}
    detail["parsed_jd"] = parsed_jd
    detail["batch_metadata"] = {
        **(detail.get("batch_metadata") if isinstance(detail.get("batch_metadata"), dict) else {}),
        "ai_reviewer_runtime": dict(safe_runtime),
    }
    detail["batch_ai_reviewer_runtime"] = dict(safe_runtime)
    normalize_ai_review_state(detail)
    return runtime


def build_runtime_ai_reviewer_scoring_config(
    scoring_cfg: dict[str, Any] | None,
    runtime_cfg: dict[str, Any] | None,
) -> dict[str, Any]:
    effective_scoring_cfg = deepcopy(scoring_cfg or {})
    reviewer_defaults = (
        effective_scoring_cfg.get("ai_reviewer") if isinstance(effective_scoring_cfg.get("ai_reviewer"), dict) else {}
    )
    runtime = normalize_batch_ai_runtime(runtime_cfg)
    effective_ai_cfg = {
        **default_ai_reviewer_config(),
        **reviewer_defaults,
        "enable_ai_reviewer": bool(runtime.get("enable_ai_reviewer", False)),
        "ai_reviewer_mode": "suggest_only",
        "provider": str(runtime.get("provider") or reviewer_defaults.get("provider") or "openai"),
        "model": str(runtime.get("model") or reviewer_defaults.get("model") or ""),
        "api_base": str(runtime.get("api_base") or reviewer_defaults.get("api_base") or ""),
        "api_key_mode": str(runtime.get("api_key_mode") or reviewer_defaults.get("api_key_mode") or "env_name"),
        "api_key_env_name": str(runtime.get("api_key_env_name") or reviewer_defaults.get("api_key_env_name") or ""),
        "capabilities": {
            **default_ai_reviewer_config().get("capabilities", {}),
            **(reviewer_defaults.get("capabilities") or {}),
        },
        "score_adjustment_limit": {
            **default_ai_reviewer_config().get("score_adjustment_limit", {}),
            **(reviewer_defaults.get("score_adjustment_limit") or {}),
        },
    }
    if runtime.get("api_key_value"):
        effective_ai_cfg["api_key_value"] = str(runtime.get("api_key_value") or "")
    effective_scoring_cfg["ai_reviewer"] = effective_ai_cfg
    return effective_scoring_cfg


def stable_ai_payload_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def compute_ai_input_hash(detail: dict[str, Any]) -> str:
    parsed_jd = detail.get("parsed_jd") if isinstance(detail.get("parsed_jd"), dict) else {}
    scoring_config = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
    payload = {
        "parsed_resume": detail.get("parsed_resume") if isinstance(detail.get("parsed_resume"), dict) else {},
        "scoring_config": scoring_config,
        "score_details": detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {},
        "risk_result": detail.get("risk_result") if isinstance(detail.get("risk_result"), dict) else {},
        "screening_result": detail.get("screening_result") if isinstance(detail.get("screening_result"), dict) else {},
        "evidence_snippets": detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else [],
    }
    return sha256(stable_ai_payload_dumps(payload).encode("utf-8")).hexdigest()


def normalize_ai_review_state(detail: dict[str, Any]) -> None:
    ai_suggestion = detail.get("ai_review_suggestion")
    if not isinstance(ai_suggestion, dict):
        ai_suggestion = {}
        detail["ai_review_suggestion"] = ai_suggestion
    status = str(detail.get("ai_review_status") or "").strip()
    if status not in {"ready", "failed", "not_generated", "outdated"}:
        detail["ai_review_status"] = "ready" if ai_suggestion else "not_generated"
    for key in [
        "ai_input_hash",
        "ai_prompt_version",
        "ai_generated_at",
        "ai_generated_by_name",
        "ai_generated_by_email",
        "ai_review_error",
        "ai_source",
        "ai_model",
        "ai_mode",
        "ai_generation_reason",
        "ai_refresh_reason",
    ]:
        detail[key] = str(detail.get(key) or "")
    detail["ai_generated_latency_ms"] = int(detail.get("ai_generated_latency_ms") or 0)
    refresh_ai_review_freshness(detail)


def refresh_ai_review_freshness(detail: dict[str, Any]) -> str:
    ai_suggestion = detail.get("ai_review_suggestion") if isinstance(detail.get("ai_review_suggestion"), dict) else {}
    status = str(detail.get("ai_review_status") or "").strip() or "not_generated"
    current_hash = compute_ai_input_hash(detail)
    stored_hash = str(detail.get("ai_input_hash") or "").strip()
    if status == "failed":
        return status
    if not ai_suggestion:
        detail["ai_review_status"] = "not_generated"
        return "not_generated"
    if stored_hash and current_hash == stored_hash:
        detail["ai_review_status"] = "ready"
        return "ready"
    if stored_hash and current_hash != stored_hash:
        detail["ai_review_status"] = "outdated"
        return "outdated"
    detail["ai_review_status"] = "ready"
    return "ready"


def _candidate_pool_label(screening_result: str) -> str:
    if screening_result == "推荐进入下一轮":
        return "通过候选人"
    if screening_result == "建议人工复核":
        return "待复核候选人"
    return "淘汰候选人"


def _decision_summary(result_text: str) -> str:
    mapping = {
        "推荐进入下一轮": "建议推进",
        "建议人工复核": "建议人工复核",
        "暂不推荐": "暂不推荐",
        "通过": "人工通过",
        "待复核": "人工待复核",
        "淘汰": "人工淘汰",
    }
    return mapping.get(str(result_text or "").strip(), str(result_text or "待判断"))


def _risk_level_label(level: str) -> str:
    mapping = {"low": "低风险", "medium": "中风险", "high": "高风险", "unknown": "未知风险"}
    return mapping.get(str(level or "unknown").strip().lower(), "未知风险")


def _review_summary(decision: str, risk_level: str, risk_summary: str = "") -> str:
    decision_text = _decision_summary(decision)
    risk_text = _risk_level_label(risk_level)
    risk_hint = str(risk_summary or "").strip()
    if risk_hint:
        return f"{decision_text}，风险等级：{risk_text}，重点：{risk_hint[:36]}"
    return f"{decision_text}，风险等级：{risk_text}。"


def _build_ai_review_metadata(detail: dict[str, Any]) -> dict[str, Any]:
    actions = detail.get("ai_applied_actions")
    score_details = detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {}
    score_snapshot = {
        dim_name: (dim_detail.get("score") if isinstance(dim_detail, dict) else dim_detail)
        for dim_name, dim_detail in score_details.items()
    }
    screening_result = detail.get("screening_result") if isinstance(detail.get("screening_result"), dict) else {}
    risk_result = detail.get("risk_result") if isinstance(detail.get("risk_result"), dict) else {}
    return {
        "scores": score_snapshot,
        "auto_screening_result": str(screening_result.get("screening_result") or ""),
        "auto_risk_level": str(risk_result.get("risk_level") or "unknown"),
        "screening_reasons": screening_result.get("screening_reasons") if isinstance(screening_result.get("screening_reasons"), list) else [],
        "risk_points": risk_result.get("risk_points") if isinstance(risk_result.get("risk_points"), list) else [],
        "evidence_snippets": detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else [],
        "ai_applied": bool(detail.get("ai_applied")),
        "ai_applied_actions": actions if isinstance(actions, list) else [],
        "ai_applied_by_name": str(detail.get("ai_applied_by_name") or ""),
        "ai_applied_by_email": str(detail.get("ai_applied_by_email") or ""),
        "ai_applied_at": str(detail.get("ai_applied_at") or ""),
        "ai_source": str(detail.get("ai_source") or ""),
        "ai_mode": str(detail.get("ai_mode") or ""),
        "ai_model": str(detail.get("ai_model") or ""),
        "ai_input_hash": str(detail.get("ai_input_hash") or ""),
        "ai_prompt_version": str(detail.get("ai_prompt_version") or ""),
        "ai_generated_latency_ms": int(detail.get("ai_generated_latency_ms") or 0),
        "ai_generation_reason": str(detail.get("ai_generation_reason") or ""),
        "ai_refresh_reason": str(detail.get("ai_refresh_reason") or ""),
        "ai_review_status": str(detail.get("ai_review_status") or ""),
        "ai_generated_at": str(detail.get("ai_generated_at") or ""),
        "ai_generated_by_name": str(detail.get("ai_generated_by_name") or ""),
        "ai_generated_by_email": str(detail.get("ai_generated_by_email") or ""),
        "ai_review_error": str(detail.get("ai_review_error") or ""),
        "ai_review_summary_snapshot": str(detail.get("ai_review_summary_snapshot") or ""),
        "ai_score_adjustments_snapshot": (
            detail.get("ai_score_adjustments_snapshot") if isinstance(detail.get("ai_score_adjustments_snapshot"), list) else []
        ),
        "ai_risk_adjustment_snapshot": (
            detail.get("ai_risk_adjustment_snapshot") if isinstance(detail.get("ai_risk_adjustment_snapshot"), dict) else {}
        ),
        "ai_reverted": bool(detail.get("ai_reverted")),
        "ai_reverted_actions": detail.get("ai_reverted_actions") if isinstance(detail.get("ai_reverted_actions"), list) else [],
        "ai_reverted_at": str(detail.get("ai_reverted_at") or ""),
        "ai_reverted_by_name": str(detail.get("ai_reverted_by_name") or ""),
        "ai_reverted_by_email": str(detail.get("ai_reverted_by_email") or ""),
    }


def _persist_candidate_state(
    *,
    batch_id: str,
    candidate_id: str,
    selected_row: dict[str, Any],
    detail: dict[str, Any],
    operator: dict[str, Any],
) -> bool:
    detail["evidence_bridge"] = build_evidence_bridge(
        detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {},
        detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else [],
    )
    persisted = persist_candidate_snapshot(
        batch_id=batch_id,
        candidate_id=candidate_id,
        row_payload=selected_row,
        detail_payload=detail,
        operator_user_id=str(operator.get("user_id") or ""),
        operator_name=str(operator.get("name") or ""),
        operator_email=str(operator.get("email") or ""),
        is_admin=bool(operator.get("is_admin")),
        enforce_lock=True,
    )
    if not persisted:
        return False
    review_id = str(detail.get("review_id") or "").strip()
    if review_id:
        upsert_manual_review(
            review_id=review_id,
            reviewed_by_user_id=str(operator.get("user_id") or ""),
            reviewed_by_name=str(operator.get("name") or ""),
            reviewed_by_email=str(operator.get("email") or ""),
            metadata_updates=_build_ai_review_metadata(detail),
        )
    return True


def _load_candidate_context(batch_id: str, candidate_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    payload = load_batch(batch_id)
    if not payload:
        raise ValueError("Batch not found")
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    detail = details.get(candidate_id)
    row = next((item for item in rows if str(item.get("candidate_id") or "") == candidate_id), None)
    if not isinstance(detail, dict) or not isinstance(row, dict):
        raise ValueError("Candidate not found")
    return payload, rows, detail, row


def _ensure_ai_application_baseline(detail: dict[str, Any], selected_row: dict[str, Any]) -> bool:
    if detail.get("ai_baseline_saved"):
        return False
    detail["baseline_score_details"] = deepcopy(detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {})
    detail["baseline_score_values"] = deepcopy(detail.get("score_values") if isinstance(detail.get("score_values"), dict) else {})
    detail["baseline_risk_result"] = deepcopy(detail.get("risk_result") if isinstance(detail.get("risk_result"), dict) else {})
    detail["baseline_screening_result"] = deepcopy(
        detail.get("screening_result") if isinstance(detail.get("screening_result"), dict) else {}
    )
    detail["baseline_review_summary"] = str(detail.get("review_summary") or selected_row.get(ROW_KEY_REVIEW_SUMMARY) or "")
    detail["baseline_evidence_snippets"] = deepcopy(
        detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else []
    )
    detail["baseline_timeline_updates_snapshot"] = deepcopy(
        detail.get("ai_timeline_updates_snapshot") if isinstance(detail.get("ai_timeline_updates_snapshot"), list) else []
    )
    detail["baseline_candidate_pool"] = str(selected_row.get(ROW_KEY_CANDIDATE_POOL) or "")
    detail["ai_baseline_saved"] = True
    return True


def _clear_ai_application_state(detail: dict[str, Any], *, clear_baseline: bool = True) -> None:
    detail["ai_applied"] = False
    detail["ai_applied_actions"] = []
    detail["ai_applied_by_name"] = ""
    detail["ai_applied_by_email"] = ""
    detail["ai_applied_at"] = ""
    detail["ai_reverted"] = False
    detail["ai_reverted_actions"] = []
    detail["ai_reverted_at"] = ""
    detail["ai_reverted_by_name"] = ""
    detail["ai_reverted_by_email"] = ""
    detail["ai_review_summary_snapshot"] = ""
    detail["ai_score_adjustments_snapshot"] = []
    detail["ai_risk_adjustment_snapshot"] = {}
    if clear_baseline:
        for key in [
            "ai_baseline_saved",
            "baseline_score_details",
            "baseline_score_values",
            "baseline_risk_result",
            "baseline_screening_result",
            "baseline_review_summary",
            "baseline_evidence_snippets",
            "baseline_timeline_updates_snapshot",
            "baseline_candidate_pool",
        ]:
            detail.pop(key, None)


def _restore_ai_baseline(
    detail: dict[str, Any],
    selected_row: dict[str, Any],
    *,
    restore_evidence: bool,
    restore_timeline: bool,
) -> bool:
    if not detail.get("ai_baseline_saved"):
        return False
    baseline_scores = detail.get("baseline_score_details")
    baseline_score_values = detail.get("baseline_score_values")
    baseline_risk = detail.get("baseline_risk_result")
    baseline_screening = detail.get("baseline_screening_result")
    detail["score_details"] = deepcopy(baseline_scores) if isinstance(baseline_scores, dict) else {}
    detail["score_values"] = (
        deepcopy(baseline_score_values)
        if isinstance(baseline_score_values, dict)
        else to_score_values(detail.get("score_details") or {})
    )
    detail["risk_result"] = deepcopy(baseline_risk) if isinstance(baseline_risk, dict) else {}
    detail["screening_result"] = deepcopy(baseline_screening) if isinstance(baseline_screening, dict) else {}
    detail["review_summary"] = str(detail.get("baseline_review_summary") or "")
    if restore_evidence:
        detail["evidence_snippets"] = deepcopy(
            detail.get("baseline_evidence_snippets") if isinstance(detail.get("baseline_evidence_snippets"), list) else []
        )
    if restore_timeline:
        detail["ai_timeline_updates_snapshot"] = deepcopy(
            detail.get("baseline_timeline_updates_snapshot")
            if isinstance(detail.get("baseline_timeline_updates_snapshot"), list)
            else []
        )
    selected_row[ROW_KEY_SCREENING_RESULT] = str((detail.get("screening_result") or {}).get("screening_result") or "")
    selected_row[ROW_KEY_RISK_LEVEL] = str((detail.get("risk_result") or {}).get("risk_level") or "unknown")
    selected_row["风险摘要"] = str((detail.get("risk_result") or {}).get("risk_summary") or "")
    selected_row[ROW_KEY_REVIEW_SUMMARY] = str(detail.get("review_summary") or "")
    selected_row[ROW_KEY_CANDIDATE_POOL] = str(
        detail.get("baseline_candidate_pool") or _candidate_pool_label(selected_row.get(ROW_KEY_SCREENING_RESULT, ""))
    )
    detail["evidence_bridge"] = build_evidence_bridge(
        detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {},
        detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else [],
    )
    return True


def _apply_ai_evidence_suggestions(detail: dict[str, Any], suggestions: list[dict[str, Any]]) -> int:
    if not suggestions:
        return 0
    existing = detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else []
    existing_pairs = {(str(item.get("source") or ""), str(item.get("text") or "")) for item in existing}
    added = 0
    for item in suggestions:
        source = str(item.get("source") or "AI建议")
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        pair = (source, text)
        if pair in existing_pairs:
            continue
        existing.append({"source": source, "text": text})
        existing_pairs.add(pair)
        added += 1
    detail["evidence_snippets"] = existing
    return added


def _apply_ai_timeline_updates(detail: dict[str, Any], ai_suggestion: dict[str, Any]) -> int:
    updates = ai_suggestion.get("timeline_updates") if isinstance(ai_suggestion, dict) else []
    if not isinstance(updates, list):
        return 0
    existing = detail.get("ai_timeline_updates_snapshot")
    if not isinstance(existing, list):
        existing = []
    existing_pairs = {
        (str(item.get("label") or ""), str(item.get("value") or ""))
        for item in existing
        if isinstance(item, dict)
    }
    added = 0
    for item in updates:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        if not label or not value:
            continue
        pair = (label, value)
        if pair in existing_pairs:
            continue
        existing.append({"label": label, "value": value, "note": str(item.get("note") or "").strip()})
        existing_pairs.add(pair)
        added += 1
    if added > 0:
        detail["ai_timeline_updates_snapshot"] = existing
    return added


def _apply_ai_risk_suggestion(detail: dict[str, Any], selected_row: dict[str, Any], ai_suggestion: dict[str, Any]) -> bool:
    risk_adjustment = ai_suggestion.get("risk_adjustment") if isinstance(ai_suggestion, dict) else {}
    if not isinstance(risk_adjustment, dict):
        return False
    new_level = str(risk_adjustment.get("suggested_risk_level") or "").strip().lower()
    if not new_level:
        return False
    risk_result = detail.get("risk_result") if isinstance(detail.get("risk_result"), dict) else {}
    risk_result["risk_level"] = new_level
    reason = str(risk_adjustment.get("reason") or "").strip()
    if reason:
        risk_result["risk_summary"] = reason
        selected_row["风险摘要"] = reason
    detail["risk_result"] = risk_result
    selected_row[ROW_KEY_RISK_LEVEL] = new_level
    return True


def _recalculate_overall_score(detail: dict[str, Any]) -> None:
    score_details = detail.get("score_details")
    if not isinstance(score_details, dict):
        return
    parsed_jd = detail.get("parsed_jd") if isinstance(detail.get("parsed_jd"), dict) else {}
    scoring_cfg = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
    weights = scoring_cfg.get("weights") if isinstance(scoring_cfg.get("weights"), dict) else {}
    weighted_sum = 0.0
    weight_total = 0.0
    low_count = 0
    exp_score = 1
    skill_score = 1
    for dim in BASE_DIMENSIONS:
        dim_detail = score_details.get(dim) if isinstance(score_details.get(dim), dict) else {}
        try:
            dim_score = int(dim_detail.get("score", 1) or 1)
        except (TypeError, ValueError):
            dim_score = 1
        try:
            dim_weight = float(weights.get(dim, 1.0) or 1.0)
        except (TypeError, ValueError):
            dim_weight = 1.0
        weighted_sum += dim_score * dim_weight
        weight_total += dim_weight
        if dim_score <= 2:
            low_count += 1
        if dim == "相关经历匹配度":
            exp_score = dim_score
        if dim == "技能匹配度":
            skill_score = dim_score
    if weight_total <= 0:
        return
    overall_score = round(weighted_sum / weight_total)
    if exp_score <= 2 or skill_score <= 2:
        overall_score = min(overall_score, 3)
    if low_count >= 2:
        overall_score = min(overall_score, 2)
    if not (exp_score >= 4 and skill_score >= 4):
        overall_score = min(overall_score, 4)
    overall_detail = score_details.get(OVERALL_DIMENSION) if isinstance(score_details.get(OVERALL_DIMENSION), dict) else {}
    evidence = overall_detail.get("evidence")
    if not isinstance(evidence, list):
        evidence = [str(evidence)] if evidence else []
    note = "AI 建议应用后按当前维度评分重算综合推荐度。"
    if note not in evidence:
        evidence.append(note)
    overall_detail["score"] = max(1, min(5, int(overall_score)))
    overall_detail["reason"] = overall_detail.get("reason") or "综合推荐度已根据当前维度评分重算。"
    overall_detail["evidence"] = evidence
    score_details[OVERALL_DIMENSION] = overall_detail
    detail["score_details"] = score_details


def _apply_ai_score_suggestions(detail: dict[str, Any], selected_row: dict[str, Any], ai_suggestion: dict[str, Any]) -> int:
    adjustments = ai_suggestion.get("score_adjustments") if isinstance(ai_suggestion, dict) else []
    if not isinstance(adjustments, list):
        return 0
    score_details = detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {}
    applied = 0
    for item in adjustments:
        if not isinstance(item, dict):
            continue
        dim = str(item.get("dimension") or "").strip()
        if dim not in score_details:
            continue
        current = score_details.get(dim) if isinstance(score_details.get(dim), dict) else {}
        try:
            base_score = int(current.get("score", 0) or 0)
            delta = int(item.get("suggested_delta", 0) or 0)
            max_delta = int(item.get("max_delta", 1) or 1)
        except (TypeError, ValueError):
            continue
        bounded = max(-max_delta, min(max_delta, delta))
        new_score = max(1, min(5, base_score + bounded))
        evidence = current.get("evidence")
        if not isinstance(evidence, list):
            evidence = [str(evidence)] if evidence else []
        note = f"AI建议修正：{item.get('reason') or '无'}"
        if note not in evidence:
            evidence.append(note)
        if new_score == base_score and bounded == 0:
            continue
        current["score"] = new_score
        current["evidence"] = evidence
        score_details[dim] = current
        if dim in BASE_DIMENSIONS:
            selected_row[dim] = new_score
        applied += 1
    detail["score_details"] = score_details
    if applied > 0:
        _recalculate_overall_score(detail)
        detail["score_values"] = to_score_values(detail["score_details"])
    return applied


def _merge_ai_actions(existing_actions: list[str], new_actions: list[str]) -> list[str]:
    merged: list[str] = []
    for action in [*(existing_actions or []), *(new_actions or [])]:
        clean = str(action or "").strip()
        if clean and clean not in merged:
            merged.append(clean)
    return merged


def _refresh_candidate_after_ai_application(detail: dict[str, Any], selected_row: dict[str, Any], ai_cfg: dict[str, Any]) -> None:
    score_details = detail.get("score_details")
    if not isinstance(score_details, dict):
        detail["score_details"] = {}
    _recalculate_overall_score(detail)
    detail["score_values"] = to_score_values(detail.get("score_details") or {})
    risk_result = detail.get("risk_result") if isinstance(detail.get("risk_result"), dict) else {}
    risk_level = str(risk_result.get("risk_level") or selected_row.get(ROW_KEY_RISK_LEVEL) or "unknown").lower()
    risk_result["risk_level"] = risk_level
    risk_summary = str(selected_row.get("风险摘要") or risk_result.get("risk_summary") or "").strip()
    if risk_summary:
        risk_result["risk_summary"] = risk_summary
    detail["risk_result"] = risk_result
    current_screening = detail.get("screening_result")
    if not isinstance(current_screening, dict):
        current_screening = {}
    allow_direct_change = bool(
        ((ai_cfg.get("score_adjustment_limit") or {}).get("allow_direct_recommendation_change", False))
    )
    if allow_direct_change:
        current_screening = build_screening_decision(
            scores_input=detail.get("score_details") or {},
            risk_level=risk_level,
            risks=risk_result.get("risk_points", []),
            scoring_config=((detail.get("parsed_jd") or {}).get("scoring_config") or {}),
        )
        selected_row[ROW_KEY_SCREENING_RESULT] = current_screening.get("screening_result", "")
        selected_row[ROW_KEY_CANDIDATE_POOL] = _candidate_pool_label(selected_row.get(ROW_KEY_SCREENING_RESULT, ""))
    else:
        current_screening.setdefault("screening_result", str(selected_row.get(ROW_KEY_SCREENING_RESULT) or ""))
        current_screening.setdefault("screening_reasons", current_screening.get("screening_reasons") or [])
    detail["screening_result"] = current_screening
    auto_decision = str((detail.get("screening_result") or {}).get("screening_result") or "")
    if auto_decision:
        selected_row[ROW_KEY_SCREENING_RESULT] = auto_decision
    selected_row[ROW_KEY_RISK_LEVEL] = risk_level
    if risk_summary:
        selected_row["风险摘要"] = risk_summary
    selected_row[ROW_KEY_REVIEW_SUMMARY] = _review_summary(auto_decision, risk_level, risk_summary)
    detail["review_summary"] = selected_row[ROW_KEY_REVIEW_SUMMARY]


def generate_ai_for_candidate(
    *,
    batch_id: str,
    candidate_id: str,
    operator: dict[str, Any],
    runtime_cfg: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    _, rows, detail, selected_row = _load_candidate_context(batch_id, candidate_id)
    can_operate, _ = can_user_operate_candidate(
        batch_id=batch_id,
        candidate_id=candidate_id,
        operator_user_id=str(operator.get("user_id") or ""),
        is_admin=bool(operator.get("is_admin")),
    )
    if not can_operate:
        raise PermissionError("Current candidate is locked by another reviewer")
    normalize_ai_review_state(detail)
    ai_status = refresh_ai_review_freshness(detail)
    current_input_hash = compute_ai_input_hash(detail)
    ai_suggestion = detail.get("ai_review_suggestion") if isinstance(detail.get("ai_review_suggestion"), dict) else {}
    if (
        not force_refresh
        and ai_status == "ready"
        and ai_suggestion
        and current_input_hash == str(detail.get("ai_input_hash") or "").strip()
    ):
        return {
            "ok": False,
            "message": "当前 AI 建议仍然有效，可直接查看或手动刷新。",
            "feedback_kind": "info",
            "detail": detail,
        }
    parsed_jd = detail.get("parsed_jd") if isinstance(detail.get("parsed_jd"), dict) else {}
    scoring_cfg = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
    runtime = runtime_cfg or detail.get("batch_ai_reviewer_runtime") or {}
    effective_scoring_cfg = build_runtime_ai_reviewer_scoring_config(scoring_cfg, runtime)
    ai_cfg = effective_scoring_cfg.get("ai_reviewer") if isinstance(effective_scoring_cfg.get("ai_reviewer"), dict) else {}
    ai_enabled = bool(ai_cfg.get("enable_ai_reviewer", False)) and str(ai_cfg.get("ai_reviewer_mode") or "") != "off"
    if not ai_enabled:
        raise ValueError("Current batch has not enabled AI reviewer")
    template_name = scoring_cfg.get("role_template") or scoring_cfg.get("profile_name")
    role_profile = get_profile_by_name(template_name) if template_name else detect_role_profile(parsed_jd)
    started_at = perf_counter()
    generation_reason = "manual_refresh" if force_refresh else "first_generate"
    ai_output = run_ai_reviewer(
        parsed_jd=parsed_jd,
        parsed_resume=detail.get("parsed_resume") if isinstance(detail.get("parsed_resume"), dict) else {},
        role_profile=role_profile,
        scoring_config=effective_scoring_cfg,
        score_details=detail.get("score_details") if isinstance(detail.get("score_details"), dict) else {},
        risk_result=detail.get("risk_result") if isinstance(detail.get("risk_result"), dict) else {},
        screening_result=detail.get("screening_result") if isinstance(detail.get("screening_result"), dict) else {},
        evidence_snippets=detail.get("evidence_snippets") if isinstance(detail.get("evidence_snippets"), list) else [],
        analysis_payload=detail.get("analysis_payload") if isinstance(detail.get("analysis_payload"), dict) else {},
    )
    ai_output = ai_output if isinstance(ai_output, dict) else {}
    ai_meta = ai_output.get("meta") if isinstance(ai_output.get("meta"), dict) else {}
    detail["ai_review_suggestion"] = ai_output
    detail["ai_generated_at"] = _now_str()
    detail["ai_generated_by_name"] = str(operator.get("name") or "")
    detail["ai_generated_by_email"] = str(operator.get("email") or "")
    detail["ai_review_error"] = ""
    detail["ai_source"] = str(ai_meta.get("source") or "")
    detail["ai_model"] = str(ai_meta.get("model") or ai_cfg.get("model") or "")
    detail["ai_mode"] = str(ai_output.get("mode") or ai_cfg.get("ai_reviewer_mode") or "")
    detail["ai_input_hash"] = current_input_hash
    detail["ai_prompt_version"] = str(ai_meta.get("prompt_version") or get_ai_reviewer_prompt_version())
    detail["ai_generated_latency_ms"] = int(ai_meta.get("generated_latency_ms") or int(round((perf_counter() - started_at) * 1000)))
    detail["ai_generation_reason"] = generation_reason
    detail["ai_refresh_reason"] = generation_reason if force_refresh else ""
    detail["ai_review_status"] = "ready"
    refresh_ai_review_freshness(detail)
    if not _persist_candidate_state(
        batch_id=batch_id,
        candidate_id=candidate_id,
        selected_row=selected_row,
        detail=detail,
        operator=operator,
    ):
        raise PermissionError("Candidate lock changed before AI suggestion could be saved")
    return {
        "ok": True,
        "message": "AI review suggestion generated",
        "feedback_kind": "success",
        "detail": detail,
    }


def apply_ai_suggestions_to_candidate(
    *,
    batch_id: str,
    candidate_id: str,
    operator: dict[str, Any],
    apply_evidence: bool = False,
    apply_timeline: bool = False,
    apply_risk: bool = False,
    apply_scores: bool = False,
) -> dict[str, Any]:
    _, rows, detail, selected_row = _load_candidate_context(batch_id, candidate_id)
    can_operate, _ = can_user_operate_candidate(
        batch_id=batch_id,
        candidate_id=candidate_id,
        operator_user_id=str(operator.get("user_id") or ""),
        is_admin=bool(operator.get("is_admin")),
    )
    if not can_operate:
        raise PermissionError("Current candidate is locked by another reviewer")
    ai_suggestion = detail.get("ai_review_suggestion") if isinstance(detail.get("ai_review_suggestion"), dict) else {}
    if not ai_suggestion:
        raise ValueError("AI suggestion has not been generated")
    parsed_jd = detail.get("parsed_jd") if isinstance(detail.get("parsed_jd"), dict) else {}
    scoring_cfg = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
    ai_cfg = scoring_cfg.get("ai_reviewer") if isinstance(scoring_cfg.get("ai_reviewer"), dict) else {}
    _ensure_ai_application_baseline(detail, selected_row)
    applied_actions: list[str] = []
    if apply_evidence and _apply_ai_evidence_suggestions(detail, ai_suggestion.get("evidence_updates") or []) > 0:
        applied_actions.append("evidence")
    if apply_timeline and _apply_ai_timeline_updates(detail, ai_suggestion) > 0:
        applied_actions.append("timeline")
    if apply_risk and _apply_ai_risk_suggestion(detail, selected_row, ai_suggestion):
        applied_actions.append("risk")
    if apply_scores and _apply_ai_score_suggestions(detail, selected_row, ai_suggestion) > 0:
        applied_actions.append("scores")
    if not applied_actions:
        return {"ok": False, "message": "No new AI suggestion was applied", "detail": detail}
    _refresh_candidate_after_ai_application(detail, selected_row, ai_cfg)
    detail["ai_applied"] = True
    detail["ai_applied_actions"] = _merge_ai_actions(
        detail.get("ai_applied_actions") if isinstance(detail.get("ai_applied_actions"), list) else [],
        applied_actions,
    )
    detail["ai_applied_by_name"] = str(operator.get("name") or "")
    detail["ai_applied_by_email"] = str(operator.get("email") or "")
    detail["ai_applied_at"] = _now_str()
    detail["ai_source"] = str((ai_suggestion.get("meta") or {}).get("source") or "")
    detail["ai_mode"] = str(ai_suggestion.get("mode") or ai_cfg.get("ai_reviewer_mode") or "")
    detail["ai_model"] = str((ai_suggestion.get("meta") or {}).get("model") or ai_cfg.get("model") or "")
    detail["ai_reverted"] = False
    detail["ai_reverted_actions"] = []
    detail["ai_reverted_at"] = ""
    detail["ai_reverted_by_name"] = ""
    detail["ai_reverted_by_email"] = ""
    detail["ai_review_summary_snapshot"] = str(ai_suggestion.get("review_summary") or "")
    detail["ai_score_adjustments_snapshot"] = (
        ai_suggestion.get("score_adjustments") if isinstance(ai_suggestion.get("score_adjustments"), list) else []
    )
    detail["ai_risk_adjustment_snapshot"] = (
        ai_suggestion.get("risk_adjustment") if isinstance(ai_suggestion.get("risk_adjustment"), dict) else {}
    )
    refresh_ai_review_freshness(detail)
    if not _persist_candidate_state(
        batch_id=batch_id,
        candidate_id=candidate_id,
        selected_row=selected_row,
        detail=detail,
        operator=operator,
    ):
        raise PermissionError("Candidate lock changed before AI application could be saved")
    return {"ok": True, "message": "AI suggestion applied", "detail": detail}


def revert_ai_application_for_candidate(
    *,
    batch_id: str,
    candidate_id: str,
    operator: dict[str, Any],
    full_restore: bool,
) -> dict[str, Any]:
    _, rows, detail, selected_row = _load_candidate_context(batch_id, candidate_id)
    can_operate, _ = can_user_operate_candidate(
        batch_id=batch_id,
        candidate_id=candidate_id,
        operator_user_id=str(operator.get("user_id") or ""),
        is_admin=bool(operator.get("is_admin")),
    )
    if not can_operate:
        raise PermissionError("Current candidate is locked by another reviewer")
    applied_actions = detail.get("ai_applied_actions") if isinstance(detail.get("ai_applied_actions"), list) else []
    if not detail.get("ai_baseline_saved") or (not applied_actions and not detail.get("ai_applied")):
        return {"ok": False, "message": "No applied AI suggestion to revert", "detail": detail}
    restored = _restore_ai_baseline(
        detail,
        selected_row,
        restore_evidence=full_restore,
        restore_timeline=full_restore,
    )
    if not restored:
        return {"ok": False, "message": "Failed to restore baseline", "detail": detail}
    reverted_actions = deepcopy(applied_actions)
    if full_restore:
        _clear_ai_application_state(detail, clear_baseline=True)
    else:
        remaining_actions = [action for action in applied_actions if action not in {"scores", "risk"}]
        detail["ai_applied"] = bool(remaining_actions)
        detail["ai_applied_actions"] = remaining_actions
        if not remaining_actions:
            detail["ai_applied_by_name"] = ""
            detail["ai_applied_by_email"] = ""
            detail["ai_applied_at"] = ""
    detail["ai_reverted"] = True
    detail["ai_reverted_at"] = _now_str()
    detail["ai_reverted_by_name"] = str(operator.get("name") or "")
    detail["ai_reverted_by_email"] = str(operator.get("email") or "")
    detail["ai_reverted_actions"] = reverted_actions
    refresh_ai_review_freshness(detail)
    if not _persist_candidate_state(
        batch_id=batch_id,
        candidate_id=candidate_id,
        selected_row=selected_row,
        detail=detail,
        operator=operator,
    ):
        raise PermissionError("Candidate lock changed before AI revert could be saved")
    return {"ok": True, "message": "AI suggestion reverted", "detail": detail}


def clear_ai_application_state_for_candidate(
    *,
    batch_id: str,
    candidate_id: str,
    operator: dict[str, Any],
) -> dict[str, Any]:
    _, rows, detail, selected_row = _load_candidate_context(batch_id, candidate_id)
    can_operate, _ = can_user_operate_candidate(
        batch_id=batch_id,
        candidate_id=candidate_id,
        operator_user_id=str(operator.get("user_id") or ""),
        is_admin=bool(operator.get("is_admin")),
    )
    if not can_operate:
        raise PermissionError("Current candidate is locked by another reviewer")
    has_state = bool(detail.get("ai_applied") or detail.get("ai_baseline_saved") or detail.get("ai_reverted"))
    if not has_state:
        return {"ok": False, "message": "No AI application state to clear", "detail": detail}
    _clear_ai_application_state(detail, clear_baseline=True)
    refresh_ai_review_freshness(detail)
    if not _persist_candidate_state(
        batch_id=batch_id,
        candidate_id=candidate_id,
        selected_row=selected_row,
        detail=detail,
        operator=operator,
    ):
        raise PermissionError("Candidate lock changed before AI state could be cleared")
    return {"ok": True, "message": "AI application state cleared", "detail": detail}


def test_runtime_connection(runtime_cfg: dict[str, Any], *, purpose: str = "generic") -> dict[str, Any]:
    runtime = normalize_batch_ai_runtime(runtime_cfg)
    return test_ai_connection(runtime, purpose=purpose)
