"""初筛编排模块（结论规则增强版）。

模块职责：
- 串联 JD 解析、简历解析、评分、风险分析和面试建议。
- 根据五维评分 + 风险等级规则输出最终初筛结论。
- 输出结构化结果，供 app.py 渲染与后续扩展。
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from src.ai_reviewer import run_ai_reviewer
from src.interviewer import build_interview_plan
from src.jd_parser import parse_jd
from src.rag import build_evidence_grounding
from src.resume_parser import parse_resume
from src.role_profiles import DEFAULT_SCREENING_THRESHOLDS, detect_role_profile, get_profile_by_name
from src.risk_analyzer import analyze_risk
from src.scorer import SCORE_DIMENSION_ORDER, hydrate_representative_evidence, score_candidate, to_score_values


METHOD_SIGNAL_KEYWORDS = [
    "需求分析",
    "PRD",
    "原型",
    "原型设计",
    "SQL",
    "Python",
    "用户访谈",
    "问卷",
    "问卷设计",
    "可用性测试",
    "可用性",
    "指标",
    "A/B",
    "A/B测试",
    "AB测试",
]
RESULT_SIGNAL_KEYWORDS = [
    "提升",
    "降低",
    "增长",
    "优化",
    "转化",
    "效率",
    "结论",
    "留存",
    "复盘",
    "洞察",
    "上线",
]
EDUCATION_SIGNAL_KEYWORDS = ["本科", "硕士", "博士", "研究生", "大学", "学院", "专业", "毕业"]
RISK_SIGNAL_KEYWORDS = ["协助", "参与", "了解", "熟悉", "接触", "辅助"]
TIME_PATTERN = re.compile(r"(19|20)\d{2}(?:[./-]\d{1,2}|年\d{1,2}月)?")
LINE_SPLIT_PATTERN = re.compile(r"[\n。；;！？!?]+")
CLAUSE_SPLIT_PATTERN = re.compile(r"[，,、|｜]+")


def _ensure_score_values(scores_input: dict[str, Any]) -> dict[str, int]:
    """兼容两种输入：
    1) 详细评分 dict（每项含 score/reason/evidence）
    2) 纯分数字典
    """
    if not scores_input:
        return {}

    # 详细评分结构
    sample = next(iter(scores_input.values()))
    if isinstance(sample, dict) and "score" in sample:
        return {k: int(v.get("score", 1)) for k, v in scores_input.items()}

    # 纯分数字典
    return {k: int(v) for k, v in scores_input.items()}


def _infer_risk_level_from_risks(risks: list[str]) -> str | None:
    """风险等级占位推断：
    - 外部明确传入 risk_level 时，不使用该函数。
    - 未接入风险等级模块时，可根据风险文案做弱推断。
    """
    if not risks:
        return None

    joined = " ".join(risks)
    if any(k in joined for k in ["真实性", "关键事实缺失", "严重缺失"]):
        return "high"
    if any(k in joined for k in ["建议核验", "信息较少", "证据不足"]):
        return "medium"
    return "low"


def _get_score_detail(scores_input: dict[str, Any], dimension: str) -> dict[str, Any]:
    value = scores_input.get(dimension) if isinstance(scores_input, dict) else None
    return value if isinstance(value, dict) else {}


def _normalize_reason_key(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").strip().lower())


def _append_reason(reasons: list[str], text: str, limit: int = 4) -> None:
    clean = str(text or "").strip().rstrip("。；;，, ")
    if not clean or len(reasons) >= limit:
        return

    key = _normalize_reason_key(clean)
    if not key:
        return

    for existing in reasons:
        existing_key = _normalize_reason_key(existing)
        if not existing_key:
            continue
        if key == existing_key or key in existing_key or existing_key in key:
            return

    reasons.append(clean + "。")


def _extract_skill_hit_summary(skill_detail: dict[str, Any]) -> str:
    for item in skill_detail.get("evidence", []) or []:
        match = re.search(r"JD 必备技能命中[:：]\s*(\d+)\s*/\s*(\d+)", str(item))
        if match:
            return f"必备技能命中 {match.group(1)}/{match.group(2)}"
    return ""


def _pick_nonredundant_risk_point(
    risk_points: list[str],
    existing_reasons: list[str],
    blocked_keywords: list[str] | None = None,
) -> str:
    blocked = [str(keyword or "").strip() for keyword in (blocked_keywords or []) if str(keyword or "").strip()]
    existing_blob = " ".join(existing_reasons)

    for point in risk_points or []:
        clean = str(point or "").strip()
        if not clean:
            continue
        if any(keyword in clean and keyword in existing_blob for keyword in blocked):
            continue
        if _normalize_reason_key(clean) in _normalize_reason_key(existing_blob):
            continue
        return clean
    return ""


def _normalize_bridge_text(text: str) -> str:
    return _normalize_match_text(
        re.sub(r"^(代表片段（.+?）|原文片段|命中必备技能|简历学历/专业|结构完整度|评分说明|代表证据)[:：]\s*", "", str(text or "").strip())
    )


def _extract_bridge_tokens(text: str) -> set[str]:
    tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9+/.#-]*|[\u4e00-\u9fff]{2,}", str(text or "")))
    return {token.lower() for token in tokens if len(token) >= 2}


def _evidence_link_score(rep_text: str, snippet_text: str) -> int:
    rep_norm = _normalize_bridge_text(rep_text)
    snippet_norm = _normalize_bridge_text(snippet_text)
    if not rep_norm or not snippet_norm:
        return 0
    if rep_norm == snippet_norm:
        return 120

    shorter, longer = (rep_norm, snippet_norm) if len(rep_norm) <= len(snippet_norm) else (snippet_norm, rep_norm)
    if len(shorter) >= 8 and shorter in longer:
        return 96

    rep_tokens = _extract_bridge_tokens(rep_text)
    snippet_tokens = _extract_bridge_tokens(snippet_text)
    overlap = rep_tokens & snippet_tokens
    if not overlap:
        return 0
    return len(overlap) * 12 + max(len(token) for token in overlap)


def build_evidence_bridge(score_details: dict[str, Any], evidence_snippets: list[dict[str, Any]]) -> dict[str, Any]:
    hydrated_scores = hydrate_representative_evidence(score_details if isinstance(score_details, dict) else {})

    prepared_snippets: list[dict[str, Any]] = []
    for index, item in enumerate(evidence_snippets or [], start=1):
        if not isinstance(item, dict):
            item = {"source": "其他", "text": str(item or "")}
        prepared = dict(item)
        prepared["snippet_id"] = str(prepared.get("snippet_id") or f"snippet-{index}")
        related_dimensions = prepared.get("related_dimensions")
        prepared["related_dimensions"] = list(related_dimensions) if isinstance(related_dimensions, list) else []
        prepared_snippets.append(prepared)

    dimension_evidence: list[dict[str, Any]] = []
    for dimension in SCORE_DIMENSION_ORDER:
        detail = hydrated_scores.get(dimension)
        if not isinstance(detail, dict):
            continue

        representative = detail.get("representative_evidence") if isinstance(detail.get("representative_evidence"), dict) else {}
        rep_display_text = str(representative.get("display_text") or representative.get("text") or "").strip()
        rep_raw_text = str(representative.get("raw_text") or representative.get("raw") or rep_display_text).strip()
        if not rep_display_text:
            continue

        entry = {
            "dimension": dimension,
            "score": int(detail.get("score", 1) or 1),
            "label": str(representative.get("label") or "代表证据"),
            "display_text": rep_display_text,
            "raw_text": rep_raw_text,
            "text": rep_display_text,
            "raw": rep_raw_text,
            "tags": list(representative.get("tags") or []) if isinstance(representative.get("tags"), list) else [],
            "is_low_readability": bool(representative.get("is_low_readability")),
            "linked_snippet_id": "",
            "linked_snippet_tag": "",
        }

        best_match: dict[str, Any] | None = None
        best_score = 0
        for snippet in prepared_snippets:
            candidate_score = _evidence_link_score(entry["display_text"], str(snippet.get("text") or ""))
            if candidate_score > best_score:
                best_score = candidate_score
                best_match = snippet

        if best_match is not None and best_score >= 18:
            entry["linked_snippet_id"] = str(best_match.get("snippet_id") or "")
            entry["linked_snippet_tag"] = str(best_match.get("tag") or "")
            related_dimensions = best_match.get("related_dimensions")
            if not isinstance(related_dimensions, list):
                related_dimensions = []
                best_match["related_dimensions"] = related_dimensions
            if dimension not in related_dimensions:
                related_dimensions.append(dimension)
            snippet_text = str(best_match.get("text") or "").strip()
            if snippet_text and (entry.get("is_low_readability") or len(entry.get("display_text") or "") < 18):
                entry["display_text"] = snippet_text
                entry["text"] = snippet_text
                if not entry.get("raw_text"):
                    entry["raw_text"] = snippet_text
                tags = entry.get("tags")
                if not isinstance(tags, list):
                    tags = []
                if "来自关键证据" not in tags:
                    tags.append("来自关键证据")
                entry["tags"] = tags
            elif snippet_text and best_score >= 48 and snippet_text not in str(entry.get("display_text") or ""):
                entry["display_text"] = snippet_text
                entry["text"] = snippet_text
                tags = entry.get("tags")
                if not isinstance(tags, list):
                    tags = []
                if "摘要对齐" not in tags:
                    tags.append("摘要对齐")
                entry["tags"] = tags

            if snippet_text and _normalize_bridge_text(snippet_text) == _normalize_bridge_text(entry.get("display_text", "")):
                best_match["hide_in_summary"] = True

        detail["representative_evidence"] = entry
        dimension_evidence.append(entry)

    return {
        "score_details": hydrated_scores,
        "dimension_evidence": dimension_evidence,
        "summary_snippets": prepared_snippets,
    }


def build_screening_decision(
    scores_input: dict[str, Any],
    risk_level: str | None = None,
    risks: list[str] | None = None,
    scoring_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据评分与风险修正规则输出最终初筛结论。

    输入：
    - scores_input: 详细评分结果（score_candidate）或纯分数字典（to_score_values）
    - risk_level: 可选，high/medium/low
    - risks: 可选，风险文本列表（用于无 risk_level 时的占位推断）
    - scoring_config: 可选，岗位评分配置；筛选结论与评分展示共用同一份门槛配置

    输出：
    - screening_result: 推荐进入下一轮 / 建议人工复核 / 暂不推荐
    - screening_reasons: 2-4 条结论原因
    - gating_signals: 关键门槛信号摘要
    """
    score_values = _ensure_score_values(scores_input)

    overall_score = score_values.get("综合推荐度", 1)
    exp_score = score_values.get("相关经历匹配度", 1)
    skill_score = score_values.get("技能匹配度", 1)
    expression_score = score_values.get("表达完整度", 1)
    cfg = scoring_config if isinstance(scoring_config, dict) else {}
    thresholds = cfg.get("screening_thresholds") if isinstance(cfg.get("screening_thresholds"), dict) else cfg.get("thresholds")
    thresholds = {**DEFAULT_SCREENING_THRESHOLDS, **(thresholds or {})}

    pass_line = int(thresholds.get("pass_line", DEFAULT_SCREENING_THRESHOLDS["pass_line"]) or DEFAULT_SCREENING_THRESHOLDS["pass_line"])
    review_line = int(thresholds.get("review_line", DEFAULT_SCREENING_THRESHOLDS["review_line"]) or DEFAULT_SCREENING_THRESHOLDS["review_line"])
    min_exp = int(thresholds.get("min_experience", DEFAULT_SCREENING_THRESHOLDS["min_experience"]) or DEFAULT_SCREENING_THRESHOLDS["min_experience"])
    min_skill = int(thresholds.get("min_skill", DEFAULT_SCREENING_THRESHOLDS["min_skill"]) or DEFAULT_SCREENING_THRESHOLDS["min_skill"])
    min_expression = int(thresholds.get("min_expression", DEFAULT_SCREENING_THRESHOLDS["min_expression"]) or DEFAULT_SCREENING_THRESHOLDS["min_expression"])

    gating_signals = {
        "overall_score": overall_score,
        "experience_score": exp_score,
        "skills_score": skill_score,
        "expression_score": expression_score,
        "pass_line": pass_line,
        "review_line": review_line,
        "min_experience": min_exp,
        "min_skill": min_skill,
        "min_expression": min_expression,
        "hard_gate_experience": exp_score >= min_exp,
        "hard_gate_skills": skill_score >= min_skill,
        "hard_gate_expression": expression_score >= min_expression,
    }

    # 1) 基于岗位配置的基础结论
    if overall_score >= pass_line and exp_score >= min_exp and skill_score >= min_skill and expression_score >= min_expression:
        base_result = "推荐进入下一轮"
    elif overall_score < review_line or exp_score < min_exp or skill_score < min_skill or expression_score < min_expression:
        base_result = "暂不推荐"
    else:
        base_result = "建议人工复核"

    # 2) 风险修正（参考 docs/risk_levels.md）
    normalized_risk = (risk_level or "").strip().lower()
    if not normalized_risk:
        normalized_risk = (_infer_risk_level_from_risks(risks or []) or "")

    final_result = base_result
    if normalized_risk == "high":
        # 高风险默认倾向暂不推荐；若基础结论很高也至少降到人工复核
        final_result = "建议人工复核" if base_result == "推荐进入下一轮" else "暂不推荐"
    elif normalized_risk == "medium":
        # 中风险结论不高于人工复核
        if base_result == "推荐进入下一轮":
            final_result = "建议人工复核"
    elif normalized_risk == "low":
        final_result = base_result

    # 3) 结论理由（2-4条）：优先输出 HR 可读原因，其次补一条规则摘要
    experience_detail = _get_score_detail(scores_input, "相关经历匹配度")
    skill_detail = _get_score_detail(scores_input, "技能匹配度")
    expression_detail = _get_score_detail(scores_input, "表达完整度")

    exp_pattern = str(((experience_detail.get("meta") or {}).get("experience_pattern")) or "").strip()
    exp_reason = str(experience_detail.get("reason") or "").strip()
    skill_reason = str(skill_detail.get("reason") or "").strip()
    expression_reason = str(expression_detail.get("reason") or "").strip()
    skill_hit_summary = _extract_skill_hit_summary(skill_detail)
    risk_points = [str(item or "").strip() for item in (risks or []) if str(item or "").strip()]

    reasons: list[str] = []
    blocked_risk_keywords: list[str] = []
    risk_changed_result = final_result != base_result

    if normalized_risk == "high" and risk_changed_result:
        _append_reason(
            reasons,
            f"存在高风险或关键事实待核验，风险修正后已从“{base_result}”调整为“{final_result}”，当前不建议直接推进。",
        )
    elif normalized_risk == "medium" and risk_changed_result:
        _append_reason(
            reasons,
            f"存在待核验风险点，风险修正后已从“{base_result}”调整为“{final_result}”，建议先补充核验再决定是否推进。",
        )

    if final_result == "暂不推荐":
        if exp_score <= 2 and skill_score <= 2:
            _append_reason(reasons, "岗位核心能力证据整体偏弱，相关经历与关键技能都未达到直接推进要求")

        if exp_score <= 2:
            if exp_pattern == "generic_execution_only":
                _append_reason(reasons, "与岗位直接相关的项目/实习证据不足，经历描述主要停留在通用执行层")
            elif exp_pattern == "method_without_outcome":
                _append_reason(reasons, "有一定岗位相关方法信号，但缺少明确产出或结果，相关经历暂不足以支撑推进")
            else:
                _append_reason(reasons, exp_reason or "与岗位直接相关的项目/实习证据不足，相关经历匹配度偏低")
            blocked_risk_keywords.extend(["相关经历", "项目", "实习"])
        elif exp_score == 3:
            _append_reason(reasons, exp_reason or "相关经历有一定匹配，但岗位直接相关的方法、产出或结果证据还不够完整")
            blocked_risk_keywords.extend(["相关经历", "岗位相关"])

        if skill_score <= 2:
            skill_gap_reason = "JD 关键技能命中不足，当前技能证据难以支撑岗位要求"
            if skill_hit_summary:
                skill_gap_reason = f"JD 关键技能命中不足（{skill_hit_summary}），当前技能证据难以支撑岗位要求"
            _append_reason(reasons, skill_gap_reason)
            blocked_risk_keywords.extend(["技能", "SQL", "指标", "方法技能"])
        elif skill_score == 3 and len(reasons) < 3:
            _append_reason(reasons, skill_reason or "JD 关键技能有部分命中，但项目/实习中的应用证据还不够稳定")
            blocked_risk_keywords.extend(["技能", "方法技能"])

        if expression_score == 1:
            _append_reason(reasons, "简历关键信息缺失，难以稳定判断经历真实性与岗位匹配")
            blocked_risk_keywords.extend(["关键信息", "表达完整度", "信息不足"])
        elif expression_score == 2 and len(reasons) < 3:
            _append_reason(
                reasons,
                expression_reason or "简历时间线、职责或结果信息不够完整，当前判断仍需补充材料或面试核验",
            )
            blocked_risk_keywords.extend(["时间线", "表达完整度", "信息不足"])

        if not reasons:
            _append_reason(
                reasons,
                f"整体岗位匹配度尚未达到当前岗位的推进标准（综合推荐度 {overall_score}/5，岗位复核线 {review_line}/5）",
            )
    elif final_result == "建议人工复核":
        if exp_score <= 2:
            if exp_pattern == "generic_execution_only":
                _append_reason(reasons, "与岗位直接相关的项目/实习证据不足，建议围绕真实职责与代表项目继续核验")
            elif exp_pattern == "method_without_outcome":
                _append_reason(reasons, "有一定岗位相关方法信号，但缺少明确产出或结果，建议面试追问实际贡献")
            else:
                _append_reason(reasons, exp_reason or "相关经历支撑偏弱，建议围绕岗位直接相关职责继续核验")
            blocked_risk_keywords.extend(["相关经历", "项目", "实习"])
        elif exp_score == 3:
            _append_reason(reasons, exp_reason or "相关经历有一定匹配，但岗位直接相关的职责与成果证据仍需核验")
            blocked_risk_keywords.extend(["相关经历", "岗位相关"])

        if skill_score <= 2:
            skill_gap_reason = "JD 关键技能命中不足，建议重点核验关键工具和方法是否真实可用"
            if skill_hit_summary:
                skill_gap_reason = f"JD 关键技能命中不足（{skill_hit_summary}），建议重点核验关键工具和方法是否真实可用"
            _append_reason(reasons, skill_gap_reason)
            blocked_risk_keywords.extend(["技能", "SQL", "指标", "方法技能"])
        elif skill_score == 3 and len(reasons) < 3:
            _append_reason(reasons, skill_reason or "JD 关键技能有部分命中，建议结合项目细节确认实际熟练度")
            blocked_risk_keywords.extend(["技能", "方法技能"])

        if expression_score == 1:
            _append_reason(reasons, "简历关键信息缺失，建议先补充时间线、职责和结果信息再做判断")
            blocked_risk_keywords.extend(["关键信息", "表达完整度", "信息不足"])
        elif expression_score == 2 and len(reasons) < 3:
            _append_reason(reasons, expression_reason or "简历信息不够完整，建议围绕时间线和实际产出补充核验")
            blocked_risk_keywords.extend(["时间线", "表达完整度", "信息不足"])

        if not reasons:
            _append_reason(reasons, "整体匹配度处于可讨论区间，建议围绕岗位关键能力补充追问后再决定是否推进")
    else:
        _append_reason(reasons, f"综合推荐度达到岗位推进线（{overall_score}/5），且关键门槛均已达标")
        _append_reason(reasons, "相关经历、技能和表达完整度均能支撑进入下一轮进一步核验")

    if normalized_risk == "high" and not risk_changed_result:
        _append_reason(reasons, "存在高风险或关键事实待核验，当前不建议直接推进")
    elif normalized_risk == "medium" and not risk_changed_result:
        _append_reason(reasons, "存在待核验风险点，建议围绕关键事实补充核验后再决定是否推进")

    picked_risk_point = _pick_nonredundant_risk_point(risk_points, reasons, blocked_risk_keywords)
    if picked_risk_point and (normalized_risk in {"high", "medium"} or final_result != "推荐进入下一轮"):
        prefix = "高风险关注点" if normalized_risk == "high" else "待核验点"
        _append_reason(reasons, f"{prefix}：{picked_risk_point}")

    if len(reasons) < 2:
        if final_result == "推荐进入下一轮":
            _append_reason(reasons, f"当前未识别到会阻断推进的风险修正（风险等级：{normalized_risk or '未显式给出'}）")
        else:
            _append_reason(
                reasons,
                f"规则摘要：综合推荐度 {overall_score}/5；相关经历 {exp_score}/{min_exp}；技能 {skill_score}/{min_skill}；表达完整度 {expression_score}/{min_expression}",
            )
    elif len(reasons) < 4 and final_result != "推荐进入下一轮":
        _append_reason(
            reasons,
            f"规则摘要：综合推荐度 {overall_score}/5；相关经历 {exp_score}/{min_exp}；技能 {skill_score}/{min_skill}；表达完整度 {expression_score}/{min_expression}",
        )

    reasons = reasons[:4]

    return {
        "screening_result": final_result,
        "screening_reasons": reasons,
        "gating_signals": gating_signals,
    }




def _normalize_match_text(text: str) -> str:
    return re.sub(r"[\s\u3000\-_/／|｜]+", "", (text or "").lower())


def _dedupe_keywords(keywords: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        clean = str(keyword or "").strip()
        if not clean:
            continue
        normalized = _normalize_match_text(clean)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(clean)
    return deduped


def _collect_keyword_hits(text: str, keywords: list[str]) -> list[str]:
    normalized_text = _normalize_match_text(text)
    hits: list[str] = []
    for keyword in _dedupe_keywords(keywords):
        if _normalize_match_text(keyword) in normalized_text:
            hits.append(keyword)
    return hits


def _clean_segment_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
    cleaned = re.sub(r"^[\-•·●▪◦\d.、()（）\[\]]+\s*", "", cleaned).strip()
    return cleaned.strip("，,；;。.!?！？")


def _split_fragment_units(raw_text: str) -> list[str]:
    normalized = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    candidates: list[str] = []

    for raw_line in normalized.split("\n"):
        line = _clean_segment_text(raw_line)
        if not line:
            continue
        candidates.append(line)

        sentence_parts = [_clean_segment_text(part) for part in LINE_SPLIT_PATTERN.split(line)]
        for part in sentence_parts:
            if part and part not in candidates:
                candidates.append(part)

        clause_parts = [_clean_segment_text(part) for part in CLAUSE_SPLIT_PATTERN.split(line)]
        for part in clause_parts:
            if part and part not in candidates:
                candidates.append(part)

    return candidates


def _find_match_position(text: str, keywords: list[str]) -> int:
    lowered = (text or "").lower()
    positions: list[int] = []
    for keyword in keywords:
        clean = str(keyword or "").strip()
        if not clean:
            continue
        idx = lowered.find(clean.lower())
        if idx >= 0:
            positions.append(idx)
    return min(positions) if positions else -1


def _trim_snippet(text: str, keywords: list[str], max_len: int = 96) -> str:
    clean = _clean_segment_text(text)
    if len(clean) <= max_len:
        return clean

    match_pos = _find_match_position(clean, keywords)
    if match_pos < 0:
        shortened = clean[:max_len].rsplit(" ", 1)[0].strip()
        return shortened or clean[:max_len].strip()

    start = max(0, match_pos - 20)
    end = min(len(clean), start + max_len)
    delimiters = "，,；;。.!?！？ "

    for idx in range(start, max(-1, start - 16), -1):
        if idx < len(clean) and clean[idx] in delimiters:
            start = idx + 1
            break

    for idx in range(end, min(len(clean), end + 16)):
        if clean[idx] in delimiters:
            end = idx
            break

    snippet = clean[start:end].strip("，,；;。.!?！？ ")
    return snippet if len(snippet) >= 12 else clean[:max_len].strip()


def _is_low_information_segment(text: str, *, allow_education: bool = False) -> bool:
    clean = _clean_segment_text(text)
    if len(clean) < 8:
        return True
    if not allow_education and len(clean) < 12:
        return True

    if allow_education and any(keyword in clean for keyword in EDUCATION_SIGNAL_KEYWORDS):
        return False

    informative_markers = METHOD_SIGNAL_KEYWORDS + RESULT_SIGNAL_KEYWORDS + ["负责", "主导", "推动", "分析", "设计", "搭建", "研究"]
    if any(_normalize_match_text(marker) in _normalize_match_text(clean) for marker in informative_markers):
        if re.fullmatch(r"[A-Za-z0-9/+.#\-\s]{1,18}", clean):
            return True
        return False

    non_date = TIME_PATTERN.sub("", clean)
    readable_chars = re.findall(r"[A-Za-z\u4e00-\u9fff]", non_date)
    if len(readable_chars) < 6:
        return True

    if len(clean) <= 24 and not re.search(r"[，,；;。.!?！？]", clean):
        return True
    return False


def _resolve_evidence_role_profile(parsed_jd: dict[str, Any], role_profile: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(role_profile, dict) and role_profile:
        return role_profile

    scoring_cfg = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
    template_name = scoring_cfg.get("role_template") or scoring_cfg.get("profile_name")
    if template_name:
        return get_profile_by_name(str(template_name))
    return detect_role_profile(parsed_jd)


def _build_evidence_keyword_sets(
    parsed_jd: dict[str, Any],
    role_profile: dict[str, Any],
    grounding: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    grounding_payload = grounding if isinstance(grounding, dict) else {}
    jd_keywords = _dedupe_keywords(
        [
            *(parsed_jd.get("required_skills") or []),
            *(parsed_jd.get("bonus_skills") or []),
            *(parsed_jd.get("expanded_required_skills") or []),
            *(parsed_jd.get("expanded_bonus_skills") or []),
            *(grounding_payload.get("jd_terms") or []),
        ]
    )
    method_keywords = _dedupe_keywords(
        [
            *METHOD_SIGNAL_KEYWORDS,
            *(role_profile.get("experience_method_keywords") or []),
            *(role_profile.get("experience_ai_keywords") or []),
            *(role_profile.get("experience_eval_keywords") or []),
            *(grounding_payload.get("method_terms") or []),
        ]
    )
    result_keywords = _dedupe_keywords([*RESULT_SIGNAL_KEYWORDS, *(grounding_payload.get("result_terms") or [])])
    education_keywords = _dedupe_keywords(
        [
            *EDUCATION_SIGNAL_KEYWORDS,
            str(parsed_jd.get("degree_requirement") or ""),
            str(parsed_jd.get("major_preference") or ""),
        ]
    )
    risk_keywords = _dedupe_keywords(RISK_SIGNAL_KEYWORDS)
    return {
        "jd": jd_keywords,
        "method": method_keywords,
        "result": result_keywords,
        "education": education_keywords,
        "risk": risk_keywords,
    }


def _build_experience_candidate(
    source: str,
    unit_text: str,
    keyword_sets: dict[str, list[str]],
) -> dict[str, Any] | None:
    clean = _clean_segment_text(unit_text)
    if not clean:
        return None

    jd_hits = _collect_keyword_hits(clean, keyword_sets["jd"])
    method_hits = _collect_keyword_hits(clean, keyword_sets["method"])
    result_hits = _collect_keyword_hits(clean, keyword_sets["result"])
    risk_hits = _collect_keyword_hits(clean, keyword_sets["risk"])
    if not (jd_hits or method_hits or result_hits or risk_hits):
        return None

    snippet = _trim_snippet(clean, [*jd_hits, *method_hits, *result_hits, *risk_hits])
    if _is_low_information_segment(snippet):
        return None

    score = 0
    if jd_hits and (method_hits or result_hits):
        score += 120
    elif method_hits and result_hits:
        score += 95
    elif method_hits or result_hits:
        score += 80
    elif jd_hits:
        score += 65
    elif risk_hits:
        score += 40

    score += len(jd_hits) * 14
    score += len(method_hits) * 9
    score += len(result_hits) * 10
    if TIME_PATTERN.search(snippet):
        score += 4
    if 18 <= len(snippet) <= 96:
        score += 4

    if jd_hits and (method_hits or result_hits):
        tag = "JD命中+方法/结果"
    elif result_hits:
        tag = "结果证据"
    elif method_hits:
        tag = "方法证据"
    elif jd_hits:
        tag = "JD命中"
    else:
        tag = "风险证据"

    return {
        "source": source,
        "text": snippet,
        "tag": tag,
        "_score": score,
    }


def _build_relaxed_candidate(source: str, unit_text: str) -> dict[str, Any] | None:
    clean = _clean_segment_text(unit_text)
    if not clean:
        return None
    if _is_low_information_segment(clean):
        return None
    if not (TIME_PATTERN.search(clean) or any(k in clean for k in ["负责", "参与", "推动", "设计", "分析", "产出"])):
        return None

    snippet = _trim_snippet(clean, [])
    score = 45
    if TIME_PATTERN.search(snippet):
        score += 6
    if any(k in snippet for k in ["负责", "参与", "推动", "设计", "分析"]):
        score += 6

    return {
        "source": source,
        "text": snippet,
        "tag": "经历片段",
        "_score": score,
    }


def _fallback_section_candidates(parsed_resume: dict[str, Any]) -> list[dict[str, Any]]:
    section_blocks = parsed_resume.get("section_blocks") if isinstance(parsed_resume, dict) else None
    if not isinstance(section_blocks, dict):
        return []

    candidates: list[dict[str, Any]] = []
    for label, lines in section_blocks.items():
        if not lines:
            continue
        if label in {"实习", "实习经历"}:
            source_name = "实习"
        elif label in {"项目", "项目经历"}:
            source_name = "项目"
        elif label in {"经历", "工作经历"}:
            source_name = "经历"
        else:
            source_name = ""
        if not source_name:
            continue

        block_text = "\n".join(lines) if isinstance(lines, list) else str(lines)
        for unit in _split_fragment_units(block_text):
            candidate = _build_relaxed_candidate(source_name, unit)
            if candidate is not None:
                candidates.append(candidate)

    return sorted(
        candidates,
        key=lambda item: (
            int(item.get("_score") or 0),
            len(str(item.get("text") or "")),
        ),
        reverse=True,
    )[:6]


def _build_education_candidates(parsed_resume: dict[str, Any], keyword_sets: dict[str, list[str]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    education_blob = str(parsed_resume.get("education") or "").strip()
    education_units = _split_fragment_units(education_blob) if education_blob else []

    degree = str(parsed_resume.get("degree") or "").strip()
    major = str(parsed_resume.get("major") or "").strip()
    graduation_date = str(parsed_resume.get("graduation_date") or "").strip()
    structured_parts = [part for part in [major, degree, graduation_date] if part]
    if structured_parts:
        structured_line = f"教育背景 {' / '.join(structured_parts)}"
        if structured_line not in education_units:
            education_units.insert(0, structured_line)

    for unit in education_units:
        clean = _clean_segment_text(unit)
        if not clean:
            continue

        jd_hits = _collect_keyword_hits(clean, keyword_sets["jd"])
        education_hits = _collect_keyword_hits(clean, keyword_sets["education"])
        if not (jd_hits or education_hits or TIME_PATTERN.search(clean)):
            continue

        snippet = _trim_snippet(clean, [*jd_hits, *education_hits], max_len=88)
        if _is_low_information_segment(snippet, allow_education=True):
            continue

        score = 30
        if jd_hits:
            score += 20
        if education_hits:
            score += 18
        if TIME_PATTERN.search(snippet):
            score += 4
        if 14 <= len(snippet) <= 88:
            score += 2

        candidates.append(
            {
                "source": "教育",
                "text": snippet,
                "tag": "教育信息" if not jd_hits else "教育/JD命中",
                "_score": score,
            }
        )

    return sorted(
        candidates,
        key=lambda item: (
            int(item.get("_score") or 0),
            len(str(item.get("text") or "")),
        ),
        reverse=True,
    )[:1]


def collect_evidence_snippets(
    parsed_resume: dict[str, Any],
    parsed_jd: dict[str, Any] | None = None,
    role_profile: dict[str, Any] | None = None,
    limit: int = 5,
) -> list[dict[str, str]]:
    parsed_jd_payload = parsed_jd if isinstance(parsed_jd, dict) else {}
    resolved_role_profile = _resolve_evidence_role_profile(parsed_jd_payload, role_profile)
    grounding = build_evidence_grounding(
        parsed_resume,
        parsed_jd=parsed_jd_payload,
        role_profile=resolved_role_profile,
    )
    keyword_sets = _build_evidence_keyword_sets(parsed_jd_payload, resolved_role_profile, grounding)

    ranked_candidates: list[dict[str, Any]] = []
    section_blocks = parsed_resume.get("section_blocks") if isinstance(parsed_resume, dict) else None
    if isinstance(section_blocks, dict):
        for label, lines in section_blocks.items():
            if not lines:
                continue
            if label in {"实习", "实习经历"}:
                source_name = "实习"
            elif label in {"项目", "项目经历"}:
                source_name = "项目"
            elif label in {"经历", "工作经历"}:
                source_name = "经历"
            else:
                source_name = ""

            if not source_name:
                continue

            block_text = "\n".join(lines) if isinstance(lines, list) else str(lines)
            for unit in _split_fragment_units(block_text):
                candidate = _build_experience_candidate(source_name, unit, keyword_sets)
                if candidate is not None:
                    ranked_candidates.append(candidate)

    source_specs = [
        ("实习", parsed_resume.get("internships") or []),
        ("项目", parsed_resume.get("projects") or []),
    ]

    for source_name, fragments in source_specs:
        for frag in fragments:
            raw_text = str((frag or {}).get("raw_text") or "").strip()
            if not raw_text:
                continue
            best_candidate: dict[str, Any] | None = None
            for unit in _split_fragment_units(raw_text):
                candidate = _build_experience_candidate(source_name, unit, keyword_sets)
                if candidate is None:
                    continue
                if best_candidate is None:
                    best_candidate = candidate
                    continue
                current_rank = (int(candidate.get("_score") or 0), len(str(candidate.get("text") or "")))
                best_rank = (int(best_candidate.get("_score") or 0), len(str(best_candidate.get("text") or "")))
                if current_rank > best_rank:
                    best_candidate = candidate
            if best_candidate is not None:
                ranked_candidates.append(best_candidate)

    ranked_candidates.extend(_build_education_candidates(parsed_resume, keyword_sets))

    deduped: list[dict[str, str]] = []
    seen_texts: list[str] = []
    for candidate in sorted(
        ranked_candidates,
        key=lambda item: (
            int(item.get("_score") or 0),
            len(str(item.get("text") or "")),
        ),
        reverse=True,
    ):
        text = str(candidate.get("text") or "").strip()
        if not text:
            continue
        normalized_text = _normalize_match_text(text)
        if any(
            normalized_text == seen
            or normalized_text in seen
            or seen in normalized_text
            for seen in seen_texts
        ):
            continue
        seen_texts.append(normalized_text)
        deduped.append(
            {
                "source": str(candidate.get("source") or "经历"),
                "text": text,
                "tag": str(candidate.get("tag") or "经历证据"),
            }
        )
        if len(deduped) >= limit:
            break

    if not deduped:
        for candidate in _fallback_section_candidates(parsed_resume):
            deduped.append(
                {
                    "source": str(candidate.get("source") or "经历"),
                    "text": str(candidate.get("text") or "").strip(),
                    "tag": str(candidate.get("tag") or "经历片段"),
                }
            )
            if len(deduped) >= limit:
                break

    return deduped


def run_screening(jd_text: str, resume_text: str, risk_level: str | None = None) -> dict[str, Any]:
    """端到端初筛：解析 -> 评分 -> 风险 -> 结论。"""
    parsed_jd = parse_jd(jd_text)
    parsed_resume = parse_resume(resume_text)

    score_details = score_candidate(parsed_jd, parsed_resume)
    score_values = to_score_values(score_details)

    risk_result = analyze_risk(
        resume_data=parsed_resume,
        scores_input=score_details,
        resume_text=resume_text,
    )

    decision_bundle = build_screening_decision(
        scores_input=score_details,
        risk_level=(risk_level or risk_result.get("risk_level")),
        risks=risk_result.get("risk_points", []),
        scoring_config=parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {},
    )

    interview_plan = build_interview_plan(
        parsed_jd=parsed_jd,
        parsed_resume=parsed_resume,
        scores_input=score_details,
        risk_result=risk_result,
        screening_result=decision_bundle["screening_result"],
    )
    interview_questions = interview_plan["interview_questions"]

    scoring_cfg = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}
    template_name = scoring_cfg.get("role_template") or scoring_cfg.get("profile_name")
    role_profile = get_profile_by_name(template_name) if template_name else detect_role_profile(parsed_jd)
    evidence_snippets = collect_evidence_snippets(
        parsed_resume,
        parsed_jd=parsed_jd,
        role_profile=role_profile,
    )
    evidence_bridge = build_evidence_bridge(score_details, evidence_snippets)
    score_details = evidence_bridge.get("score_details") if isinstance(evidence_bridge.get("score_details"), dict) else score_details
    evidence_snippets = evidence_bridge.get("summary_snippets") if isinstance(evidence_bridge.get("summary_snippets"), list) else evidence_snippets
    ai_review_suggestion = run_ai_reviewer(
        parsed_jd=parsed_jd,
        parsed_resume=parsed_resume,
        role_profile=role_profile,
        scoring_config=scoring_cfg,
        score_details=score_details,
        risk_result=risk_result,
        screening_result=decision_bundle,
        evidence_snippets=evidence_snippets,
        analysis_payload={},
    )

    result = {
        "decision": decision_bundle["screening_result"],
        "screening_result": decision_bundle["screening_result"],
        "screening_reasons": decision_bundle["screening_reasons"],
        "gating_signals": decision_bundle["gating_signals"],
        "scores": score_details,
        "score_values": score_values,
        "reasons": decision_bundle["screening_reasons"],
        "risk_level": risk_result["risk_level"],
        "risk_summary": risk_result["risk_summary"],
        "risk_points": risk_result["risk_points"],
        "risks": risk_result["risk_points"],
        "interview_questions": interview_questions,
        "focus_points": interview_plan["focus_points"],
        "interview_summary": interview_plan["interview_summary"],
        "evidence_snippets": evidence_snippets,
        "evidence_bridge": evidence_bridge,
        "ai_review_suggestion": ai_review_suggestion,
    }

    return result


if __name__ == "__main__":
    # 本地测试示例：
    # cd HireMate
    # python src/screener.py
    demo_scores_detail = {
        "教育背景匹配度": {"score": 4, "reason": "", "evidence": []},
        "相关经历匹配度": {"score": 4, "reason": "", "evidence": []},
        "技能匹配度": {"score": 5, "reason": "", "evidence": []},
        "表达完整度": {"score": 4, "reason": "", "evidence": []},
        "综合推荐度": {"score": 4, "reason": "", "evidence": []},
    }

    demo_scores_values = {
        "教育背景匹配度": 3,
        "相关经历匹配度": 2,
        "技能匹配度": 3,
        "表达完整度": 3,
        "综合推荐度": 3,
    }

    print("=== detail + medium risk ===")
    print(build_screening_decision(demo_scores_detail, risk_level="medium"))

    print("=== value only + no risk ===")
    print(build_screening_decision(demo_scores_values))
