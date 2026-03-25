"""评分模块（规则版，含证据解释）。

模块职责：
- 基于结构化 JD + 结构化简历输出五维评分。
- 每个维度输出 score / reason / evidence，便于解释与追溯。
- 提供分数提取函数，方便 screener.py / risk_analyzer.py 使用。
"""

from __future__ import annotations

import ast
import re
from typing import Any

from src.role_profiles import (
    AI_PM_PROFILE,
    DATA_ANALYST_PROFILE,
    DEFAULT_PROFILE,
    GENERAL_PM_PROFILE,
    USER_RESEARCH_PROFILE,
    detect_role_profile,
    get_profile_by_name,
    merge_scoring_config,
)


ScoreDetail = dict[str, Any]
DetailedScores = dict[str, ScoreDetail]
SCORE_DIMENSION_ORDER = [
    "教育背景匹配度",
    "相关经历匹配度",
    "技能匹配度",
    "表达完整度",
    "综合推荐度",
]
_REP_SUSPICIOUS_CHARS_RE = re.compile(r"[�□]")
_REP_REPEAT_NOISE_RE = re.compile(r"([?？!！~～=+_*#|/\\-])\1{2,}")
_REP_MEANINGFUL_TOKEN_RE = re.compile(
    r"(?:[\u4e00-\u9fff]{2,})|(?:[A-Za-z][A-Za-z0-9+/.#-]{1,})|(?:\d{4}[./-]\d{1,2})"
)


def _clip_score(v: int) -> int:
    return max(1, min(5, v))


def _top_evidence(items: list[str], limit: int = 3) -> list[str]:
    return items[:limit] if items else ["未发现强证据，建议面试补充核验。"]


def _snippet(text: str, max_len: int = 45) -> str:
    raw = (text or "").strip()
    return raw if len(raw) <= max_len else raw[: max_len - 1] + "…"


def _norm_skill(s: str) -> str:
    return (s or "").lower().replace(" ", "")


def _norm_text(text: str) -> str:
    return (text or "").lower().replace(" ", "")


def _collect_keyword_hits(raw_norm: str, keywords: list[str]) -> list[str]:
    hits: list[str] = []
    for keyword in keywords or []:
        normalized = _norm_text(keyword)
        if normalized and normalized in raw_norm and keyword not in hits:
            hits.append(keyword)
    return hits


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)


def _bool_cn(flag: bool) -> str:
    return "有" if flag else "无"


def _normalize_evidence_text(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").strip().lower())


def _short_dimension_label(dimension: str) -> str:
    mapping = {
        "教育背景匹配度": "教育",
        "相关经历匹配度": "经历",
        "技能匹配度": "技能",
        "表达完整度": "表达",
        "综合推荐度": "综合",
    }
    return mapping.get(str(dimension or "").strip(), str(dimension or "").strip())


def _meaningful_char_count(text: str) -> int:
    clean = str(text or "")
    return sum(1 for char in clean if char.isalnum() or "\u4e00" <= char <= "\u9fff")


def _format_evidence_literal_payload(text: str, dimension: str) -> str:
    stripped = str(text or "").strip()
    if not stripped or stripped[0] not in "[{(":
        return stripped

    try:
        payload = ast.literal_eval(stripped)
    except (SyntaxError, ValueError):
        return stripped

    if isinstance(payload, (list, tuple, set)):
        parts = [str(item).strip() for item in payload if str(item).strip()]
        return " / ".join(parts) if parts else stripped

    if isinstance(payload, dict):
        parts: list[str] = []
        for key, value in payload.items():
            key_text = _short_dimension_label(str(key))
            value_text = str(value).strip()
            if not value_text:
                continue
            parts.append(f"{key_text} {value_text}")
        return "，".join(parts) if parts else stripped

    return stripped


def _clean_representative_display_text(text: str, dimension: str) -> str:
    clean = _format_evidence_literal_payload(text, dimension)
    clean = clean.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    clean = re.sub(r"\s+", " ", clean).strip()
    clean = re.sub(r"[|｜]{2,}", " | ", clean)
    clean = re.sub(r"[·•]{2,}", "·", clean)
    clean = re.sub(r"([，。；：,;])\1{1,}", r"\1", clean)
    clean = re.sub(r"\s*([，。；：,;])\s*", r"\1", clean)
    clean = re.sub(r"\s*([/|])\s*", r" \1 ", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" ，。；：,;")
    if len(clean) > 96:
        clean = clean[:95].rstrip() + "…"
    return clean


def _looks_like_low_readability_evidence(text: str, raw_text: str = "") -> bool:
    clean = str(text or "").strip()
    raw = str(raw_text or clean)
    if not clean:
        return True

    non_space_chars = [char for char in clean if not char.isspace()]
    if not non_space_chars:
        return True

    meaningful = _meaningful_char_count(clean)
    meaningful_ratio = meaningful / len(non_space_chars)
    suspicious_hits = len(_REP_SUSPICIOUS_CHARS_RE.findall(clean))
    if re.search(r"[?？]{3,}", raw):
        suspicious_hits += 2
    if _REP_REPEAT_NOISE_RE.search(raw):
        suspicious_hits += 1

    token_hits = _REP_MEANINGFUL_TOKEN_RE.findall(clean)
    has_readable_phrase = bool(token_hits)

    if suspicious_hits >= 2:
        return True
    if meaningful < 4:
        return True
    if meaningful_ratio < 0.35:
        return True
    if len(clean) < 8 and len(token_hits) < 2:
        return True
    if len(clean) < 14 and not has_readable_phrase:
        return True
    return False


def _extract_representative_tags(dimension: str, label: str, raw_text: str, display_text: str) -> list[str]:
    blob = " ".join(
        [
            str(dimension or ""),
            str(label or ""),
            str(raw_text or ""),
            str(display_text or ""),
        ]
    ).lower()
    tags: list[str] = []

    def _add(tag: str, *keywords: str) -> None:
        if any(keyword.lower() in blob for keyword in keywords) and tag not in tags:
            tags.append(tag)

    _add("方法", "方法", "需求分析", "prd", "原型", "访谈", "问卷", "可用性", "指标", "a/b", "实验")
    _add("产出", "产出", "交付", "原型", "文档", "报告", "方案", "策略", "看板", "报表")
    _add("结果", "结果", "提升", "增长", "优化", "降低", "转化", "效率", "结论", "洞察", "复盘")
    _add("JD命中", "jd", "模板命中", "核心任务", "岗位")
    _add("技能命中", "技能", "命中必备技能", "命中加分技能", "sql 证据")
    _add("教育", "学历", "专业", "教育")
    _add("门槛/风险", "硬门槛", "最低分门槛", "风险", "待核验")

    if dimension == "技能匹配度" and "技能命中" not in tags:
        tags.append("技能命中")
    if dimension == "教育背景匹配度" and "教育" not in tags:
        tags.append("教育")
    if dimension == "表达完整度" and not tags:
        tags.append("完整度")
    if dimension == "综合推荐度":
        tags = [tag for tag in tags if tag == "门槛/风险"]
        if not tags:
            tags.append("综合")
    return tags[:4]


def _parse_representative_evidence(raw_text: str, default_label: str = "证据摘要") -> dict[str, str]:
    clean = str(raw_text or "").strip()
    if not clean:
        return {}

    matched = re.match(r"^代表片段（(.+?)）[:：]\s*(.+)$", clean)
    if matched:
        return {
            "label": matched.group(1).strip() or default_label,
            "text": matched.group(2).strip(),
            "raw": clean,
        }

    matched = re.match(r"^([^:：]{2,20})[:：]\s*(.+)$", clean)
    if matched:
        return {
            "label": matched.group(1).strip() or default_label,
            "text": matched.group(2).strip(),
            "raw": clean,
        }

    return {"label": default_label, "text": clean, "raw": clean}


def _build_representative_candidate(dimension: str, raw_text: str, default_label: str) -> dict[str, Any]:
    parsed = _parse_representative_evidence(raw_text, default_label=default_label)
    if not parsed:
        return {}

    label = str(parsed.get("label") or default_label).strip() or default_label
    raw_value = str(parsed.get("raw") or raw_text or "").strip()
    display_text = _clean_representative_display_text(parsed.get("text") or raw_value, dimension)
    is_low_readability = _looks_like_low_readability_evidence(display_text, raw_value)
    tags = _extract_representative_tags(dimension, label, raw_value, display_text)

    return {
        "label": label,
        "display_text": display_text,
        "raw_text": raw_value or display_text,
        "text": display_text,
        "raw": raw_value or display_text,
        "tags": tags,
        "is_low_readability": is_low_readability,
    }


def _representative_evidence_priority(dimension: str, raw_text: str) -> tuple[int, int]:
    clean = str(raw_text or "").strip()
    if not clean:
        return (-999, 0)

    priority = 0
    if clean.startswith("代表片段"):
        priority += 120
    elif clean.startswith("原文片段"):
        priority += 100
    elif clean.startswith("命中必备技能"):
        priority += 92
    elif clean.startswith("简历学历/专业"):
        priority += 84
    elif clean.startswith("结构完整度"):
        priority += 70
    elif clean.startswith("规则摘要"):
        priority += 18

    if "硬门槛触发" in clean or "岗位最低分门槛触发" in clean:
        priority += 95 if dimension == "综合推荐度" else 28
    if "命中必备技能" in clean and dimension == "技能匹配度":
        priority += 25
    if "简历学历/专业" in clean and dimension == "教育背景匹配度":
        priority += 18
    if "关键链路" in clean or "完整度信号" in clean or "模板命中" in clean:
        priority -= 18
    if "岗位评分模板" in clean or "岗位自定义评分配置" in clean:
        priority -= 35

    return (priority, len(clean))


def _select_representative_evidence(dimension: str, detail: ScoreDetail) -> dict[str, str]:
    evidence_items = detail.get("evidence") or []
    if not isinstance(evidence_items, list):
        evidence_items = [str(evidence_items)] if evidence_items else []

    ranked_items = sorted(
        (str(item or "").strip() for item in evidence_items if str(item or "").strip()),
        key=lambda item: _representative_evidence_priority(dimension, item),
        reverse=True,
    )
    ranked_candidates = [
        _build_representative_candidate(dimension, item, default_label="代表证据")
        for item in ranked_items
    ]
    ranked_candidates = [candidate for candidate in ranked_candidates if candidate]

    for candidate in ranked_candidates:
        if not bool(candidate.get("is_low_readability")):
            return candidate

    reason = str(detail.get("reason") or "").strip()
    reason_candidate = (
        _build_representative_candidate(dimension, reason, default_label="评分说明")
        if reason
        else {}
    )
    if reason_candidate and not bool(reason_candidate.get("is_low_readability")):
        return reason_candidate

    if ranked_candidates:
        return ranked_candidates[0]
    return reason_candidate if reason_candidate else {}


def hydrate_representative_evidence(details: DetailedScores) -> DetailedScores:
    for dimension in SCORE_DIMENSION_ORDER:
        detail = details.get(dimension)
        if not isinstance(detail, dict):
            continue

        representative = _select_representative_evidence(dimension, detail)
        if not representative:
            detail.pop("representative_evidence", None)
            continue

        existing_meta = detail.get("meta")
        meta = existing_meta if isinstance(existing_meta, dict) else {}
        meta["representative_evidence_text"] = representative.get("display_text", representative.get("text", ""))
        meta["representative_evidence_low_readability"] = bool(representative.get("is_low_readability"))
        detail["meta"] = meta
        detail["representative_evidence"] = {
            "dimension": dimension,
            "label": representative.get("label", "代表证据"),
            "display_text": representative.get("display_text", representative.get("text", "")),
            "raw_text": representative.get("raw_text", representative.get("raw", representative.get("text", ""))),
            "text": representative.get("display_text", representative.get("text", "")),
            "raw": representative.get("raw_text", representative.get("raw", representative.get("text", ""))),
            "tags": representative.get("tags", []),
            "is_low_readability": bool(representative.get("is_low_readability")),
        }
    return details


def _skill_match(required_skill: str, resume_skill: str) -> bool:
    """宽松技能匹配：完全命中 + 子串命中 + 简单同义兼容。"""
    a = _norm_skill(required_skill)
    b = _norm_skill(resume_skill)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True

    alias_map = {
        "大模型": {"llm", "大模型", "大语言模型"},
        "llm": {"llm", "大模型", "大语言模型"},
        "prompt": {"prompt", "提示词", "promptengineering", "提示词工程"},
        "rag": {"rag", "检索增强生成", "检索增强"},
        "agent": {"agent", "智能体", "aiagent"},
        "知识库": {"知识库", "knowledgebase", "kb"},
        "模型评估": {"模型评估", "评估", "evaluation", "eval"},
        "数据分析": {"数据分析", "分析", "datainsight", "dataanalysis"},
        "prd": {"prd", "需求文档", "产品需求文档"},
        "原型": {"原型", "原型设计", "axure", "figma"},
        "用户研究": {"用户研究", "访谈", "问卷", "可用性测试"},
    }
    for group in alias_map.values():
        if a in group and b in group:
            return True

    synonym_groups = [
        {"ab测试", "a/b测试", "a/b 测试"},
        {"llm", "大模型"},
        {"prompt", "提示词"},
        {"prd", "需求文档"},
    ]
    for group in synonym_groups:
        if a in group and b in group:
            return True
    return False


def _skill_group_match(skill: str, resume_skills: list[str], alias_map: dict[str, list[str]] | None = None) -> tuple[bool, str]:
    aliases = alias_map.get(str(skill), []) if isinstance(alias_map, dict) else []
    for candidate in [str(skill), *[str(item) for item in aliases]]:
        if any(_skill_match(candidate, resume_skill) for resume_skill in resume_skills):
            return True, candidate
    return False, ""


def _score_education(parsed_jd: dict[str, Any], parsed_resume: dict[str, Any]) -> ScoreDetail:
    degree_req = parsed_jd.get("degree_requirement", "")
    major_pref = parsed_jd.get("major_preference", "")

    degree = parsed_resume.get("degree", "")
    major = parsed_resume.get("major", "")
    education = parsed_resume.get("education", "")

    related_major_keywords = ["计算机", "软件", "人工智能", "数据", "统计", "信息", "算法"]
    high_related = bool(major) and any(k in major for k in related_major_keywords)

    degree_match = False
    if degree_req:
        if "本科" in degree_req and degree in {"本科", "硕士", "研究生", "博士"}:
            degree_match = True
        if "硕士" in degree_req and degree in {"硕士", "研究生", "博士"}:
            degree_match = True

    # 先按“是否满足学历门槛 + 专业相关度”分层，避免“有学历就高分”
    if degree_match and high_related:
        score = 5
        reason = "学历满足要求且专业与 AI PM 岗位高度相关。"
    elif degree_match and major:
        score = 4
        reason = "学历满足要求，但专业与岗位相关度一般。"
    elif degree_match:
        score = 3
        reason = "学历基本满足要求，但专业信息不足。"
    elif degree and high_related:
        score = 3
        reason = "专业相关但学历匹配不明确，建议人工核验。"
    elif degree:
        score = 2
        reason = "仅能确认学历信息，匹配证据有限。"
    else:
        score = 1
        reason = "教育信息缺失，难以判断教育背景匹配度。"

    evidence = [
        f"JD 学历要求：{degree_req if degree_req else '未明确'}",
        f"简历学历/专业：{degree if degree else '未提及'} / {major if major else '未提及'}",
    ]
    if education:
        evidence.append(f"原文片段：{_snippet(education)}")
    if major_pref:
        evidence.append(f"JD 专业偏好：{major_pref}")

    return {"score": _clip_score(score), "reason": reason, "evidence": _top_evidence(evidence)}


def _score_experience(parsed_resume: dict[str, Any], role_profile: dict[str, Any]) -> ScoreDetail:
    internships = parsed_resume.get("internships", []) or []
    projects = parsed_resume.get("projects", []) or []
    fragments = internships + projects

    if not fragments:
        return {
            "score": 1,
            "reason": "缺少可识别的实习/项目经历证据。",
            "evidence": ["未提取到实习经历或项目经历片段。"],
        }

    # 层次 1：经历完整度（与岗位相关性分开）
    time_hits = sum(1 for f in fragments if f.get("time_found"))
    action_hits = sum(1 for f in fragments if f.get("action_keywords"))
    result_hits = sum(1 for f in fragments if f.get("result_keywords"))
    role_hits = sum(1 for f in fragments if f.get("role_keywords"))

    # 层次 2：岗位相关性（模板化）
    generic_action_keywords = ["负责", "参与", "协助", "配合", "推动", "协调", "跟进", "支持", "落地", "分析"]
    base_output_keywords = ["文档", "报告", "方案", "原型", "策略", "结论", "洞察", "看板", "报表"]
    extra_result_keywords = ["提升", "增长", "降低", "转化", "留存", "效率", "准确率", "召回率", "结论", "洞察", "复盘"]
    pm_support_keywords = ["需求分析", "需求拆解", "prd", "需求文档", "原型", "竞品", "竞品分析", "产品方案", "上线", "复盘"]
    pm_secondary_keywords = ["用户研究", "用户访谈", "ab测试", "a/b测试"]
    data_support_keywords = ["sql", "python", "数据分析", "业务分析", "指标", "指标体系", "报表", "看板", "可视化", "实验", "ab测试", "a/b测试", "分析报告"]
    research_method_keywords = ["用户访谈", "访谈", "访谈提纲", "问卷", "问卷设计", "可用性测试", "定性研究", "定量研究", "用户研究", "研究方案"]
    research_support_keywords = research_method_keywords + ["样本", "研究报告", "洞察", "研究结论", "建议"]
    method_keywords = role_profile.get("experience_method_keywords") or []
    ai_product_keywords = role_profile.get("experience_ai_keywords") or []
    eval_keywords = role_profile.get("experience_eval_keywords") or []
    core_keywords = role_profile.get("experience_core_keywords") or method_keywords
    output_keywords = list(dict.fromkeys((role_profile.get("experience_output_keywords") or []) + base_output_keywords))

    role_kind = "default"
    role_focus_label = "岗位核心方法与产出"
    if role_profile is AI_PM_PROFILE:
        role_kind = "ai_pm"
        role_focus_label = "AI 产品方法与模型/业务落地"
    elif role_profile is GENERAL_PM_PROFILE:
        role_kind = "general_pm"
        role_focus_label = "产品需求分析与方案落地"
    elif role_profile is DATA_ANALYST_PROFILE:
        role_kind = "data_analyst"
        role_focus_label = "数据分析方法与业务洞察"
    elif role_profile is USER_RESEARCH_PROFILE:
        role_kind = "user_research"
        role_focus_label = "研究方法与洞察输出"

    ai_hits = 0
    method_hits = 0
    eval_hits = 0
    output_hits = 0
    result_signal_hits = 0
    generic_hits = 0
    role_core_hits = 0

    ai_fragments = 0
    method_fragments = 0
    eval_fragments = 0
    method_signal_fragments = 0
    output_fragments = 0
    result_signal_fragments = 0
    role_core_fragments = 0
    generic_fragments = 0
    generic_only_fragments = 0
    strong_fragments = 0
    triad_fragments = 0

    method_signal_keywords: list[str] = []
    output_signal_keywords: list[str] = []
    result_signal_keywords: list[str] = []
    fragment_candidates: list[dict[str, Any]] = []
    normalized_fragments: list[str] = []

    for fragment in fragments:
        raw = (fragment.get("raw_text") or "").strip()
        raw_norm = _norm_text(raw)
        normalized_fragments.append(raw_norm)

        ai_matches = _collect_keyword_hits(raw_norm, ai_product_keywords)
        method_matches = _collect_keyword_hits(raw_norm, method_keywords)
        eval_matches = _collect_keyword_hits(raw_norm, eval_keywords)
        role_core_matches = _collect_keyword_hits(raw_norm, core_keywords)
        output_matches = _collect_keyword_hits(raw_norm, output_keywords)
        extra_result_matches = _collect_keyword_hits(raw_norm, extra_result_keywords)
        parsed_result_matches = fragment.get("result_keywords") or []
        result_matches = list(dict.fromkeys(parsed_result_matches + extra_result_matches))
        generic_matches = _collect_keyword_hits(raw_norm, generic_action_keywords)

        matched_method = bool(ai_matches or method_matches or eval_matches)
        matched_output = bool(output_matches)
        matched_result = bool(result_matches)
        matched_role_core = bool(role_core_matches)
        matched_generic = bool(generic_matches)
        matched_generic_only = matched_generic and not (matched_method or matched_output or matched_result or matched_role_core)
        matched_strong = matched_method and (matched_output or matched_result) and matched_role_core
        matched_triad = matched_method and matched_output and matched_result and matched_role_core

        ai_hits += len(ai_matches)
        method_hits += len(method_matches)
        eval_hits += len(eval_matches)
        output_hits += len(output_matches)
        result_signal_hits += len(result_matches)
        generic_hits += len(generic_matches)
        role_core_hits += len(role_core_matches)

        if ai_matches:
            ai_fragments += 1
        if method_matches:
            method_fragments += 1
        if eval_matches:
            eval_fragments += 1
        if matched_method:
            method_signal_fragments += 1
        if output_matches:
            output_fragments += 1
        if result_matches:
            result_signal_fragments += 1
        if role_core_matches:
            role_core_fragments += 1
        if generic_matches:
            generic_fragments += 1
        if matched_generic_only:
            generic_only_fragments += 1
        if matched_strong:
            strong_fragments += 1
        if matched_triad:
            triad_fragments += 1

        _extend_unique(method_signal_keywords, ai_matches + method_matches + eval_matches + role_core_matches)
        _extend_unique(output_signal_keywords, output_matches)
        _extend_unique(result_signal_keywords, result_matches)

        candidate_labels: list[str] = []
        if matched_role_core:
            candidate_labels.append("模板命中")
        if matched_method:
            candidate_labels.append("方法")
        if matched_output:
            candidate_labels.append("产出")
        if matched_result:
            candidate_labels.append("结果")
        if matched_generic_only or (matched_generic and not candidate_labels):
            candidate_labels.append("通用执行")

        rank = 0
        if matched_triad:
            rank += 7
        elif matched_strong:
            rank += 5
        elif matched_method and (matched_output or matched_result):
            rank += 4
        elif matched_method:
            rank += 2
        if matched_role_core:
            rank += 2
        if matched_output:
            rank += 1
        if matched_result:
            rank += 1
        if matched_generic_only:
            rank -= 1

        focus_keywords = list(dict.fromkeys((ai_matches + method_matches + eval_matches + role_core_matches + output_matches + result_matches)[:3]))
        fragment_candidates.append(
            {
                "rank": rank,
                "raw": raw,
                "label": "+".join(candidate_labels) if candidate_labels else "一般经历",
                "keywords": focus_keywords,
                "generic_only": matched_generic_only,
            }
        )

    has_ai_or_method = (ai_fragments + method_fragments + eval_fragments) > 0
    has_output_signal = output_fragments > 0
    has_result_signal = result_signal_fragments > 0
    has_role_core_support = role_core_fragments > 0
    has_method_output_result = triad_fragments > 0
    has_method_and_output_or_result = strong_fragments > 0 or (has_ai_or_method and (has_output_signal or has_result_signal))

    has_prd_or_prototype = any(
        any(keyword in raw_norm for keyword in ["prd", "需求文档", "原型", "axure", "figma"]) for raw_norm in normalized_fragments
    )
    has_pm_project_support = has_role_core_support and any(
        any(keyword in raw_norm for keyword in pm_support_keywords) for raw_norm in normalized_fragments
    )
    has_pm_secondary_support = any(
        any(keyword in raw_norm for keyword in pm_secondary_keywords) for raw_norm in normalized_fragments
    )
    has_data_project_support = has_role_core_support and any(
        any(keyword in raw_norm for keyword in data_support_keywords) for raw_norm in normalized_fragments
    )
    has_research_project_support = has_role_core_support and any(
        any(keyword in raw_norm for keyword in research_method_keywords) for raw_norm in normalized_fragments
    )
    has_ai_project_support = ai_fragments > 0
    has_sql_or_metric_support = any(
        any(keyword in raw_norm for keyword in ["sql", "指标", "指标体系", "报表", "看板", "可视化", "实验", "ab测试", "a/b测试", "python"])
        for raw_norm in normalized_fragments
    )
    has_research_method_support = any(
        any(keyword in raw_norm for keyword in research_method_keywords)
        for raw_norm in normalized_fragments
    )
    market_activity_only = bool(normalized_fragments) and all(
        any(keyword in raw_norm for keyword in ["活动", "运营", "拉新", "投放", "传播", "社媒", "增长运营"])
        and not any(keyword in raw_norm for keyword in research_support_keywords)
        for raw_norm in normalized_fragments
    )

    role_high_support = has_role_core_support
    role_top_support = has_method_output_result and has_role_core_support
    if role_kind == "ai_pm":
        role_high_support = has_pm_project_support and has_ai_project_support and (has_prd_or_prototype or has_output_signal)
        role_top_support = role_high_support and has_prd_or_prototype and has_method_output_result
    elif role_kind == "general_pm":
        role_high_support = has_pm_project_support and (has_prd_or_prototype or has_pm_secondary_support)
        role_top_support = role_high_support and has_prd_or_prototype and has_method_output_result
    elif role_kind == "data_analyst":
        role_high_support = has_data_project_support and has_sql_or_metric_support
        role_top_support = role_high_support and has_method_output_result
    elif role_kind == "user_research":
        role_high_support = has_research_project_support and has_research_method_support and not market_activity_only
        role_top_support = role_high_support and has_method_output_result and has_output_signal

    score = 1
    if fragments:
        score = 2
    if has_ai_or_method or has_role_core_support:
        score = 3
    if has_method_and_output_or_result and role_high_support:
        score = 4
    if has_method_output_result and role_top_support:
        score = 5

    if not has_ai_or_method and generic_fragments > 0:
        score = min(score, 2, int(role_profile.get("hard_cap_when_generic_only") or 3))
    elif has_ai_or_method and not (has_output_signal or has_result_signal):
        score = min(score, 3)

    if role_kind == "ai_pm" and not has_ai_project_support:
        score = min(score, 3)
    if role_kind in {"ai_pm", "general_pm"} and score >= 4 and not has_prd_or_prototype and not has_output_signal:
        score = min(score, 3)
    if role_kind == "data_analyst" and score >= 4 and not has_sql_or_metric_support:
        score = min(score, 3)
    if role_kind == "user_research" and market_activity_only:
        score = min(score, 2 if not has_research_method_support else 3)

    score = _clip_score(score)

    method_preview_values = method_signal_keywords[:3]
    output_preview_values = [value for value in output_signal_keywords if value not in method_preview_values][:3]
    result_preview_values = [
        value
        for value in result_signal_keywords
        if value not in method_preview_values and value not in output_preview_values
    ][:3] or result_signal_keywords[:3]

    method_preview = "、".join(method_preview_values) if method_preview_values else ""
    output_preview = "、".join(output_preview_values) if output_preview_values else ""
    result_preview = "、".join(result_preview_values) if result_preview_values else ""

    experience_pattern = "limited_relevance"
    if not has_ai_or_method and generic_fragments > 0:
        experience_pattern = "generic_execution_only"
        reason = f"经历主要停留在“负责/推动/协调”等通用执行描述，缺少与 {role_focus_label} 直接相关的方法、产出和结果证据。"
    elif has_ai_or_method and not (has_output_signal or has_result_signal):
        experience_pattern = "method_without_outcome"
        reason = (
            f"已出现 {method_preview or '岗位相关方法'} 等方法信号，但缺少明确产出或结果，"
            "相关经历暂不宜给到高分。"
        )
    elif score >= 5:
        experience_pattern = "strong_template_match"
        reason = (
            f"同时呈现 {method_preview or '岗位相关方法'} 等方法，具备 {output_preview or '关键交付'} 与 "
            f"{result_preview or '结果信号'}，且与 {role_focus_label} 高度匹配。"
        )
    elif score == 4:
        experience_pattern = "method_output_or_result"
        reason = (
            f"具备 {method_preview or '岗位相关方法'}，并给出了 {output_preview or result_preview or '产出/结果'} 等证据，"
            f"与 {role_focus_label} 的核心任务较为匹配。"
        )
    else:
        experience_pattern = "partial_template_match"
        reason = (
            f"有 {method_preview or '部分岗位相关方法'} 与 {output_preview or result_preview or '零散产出/结果'} 证据，"
            f"但与 {role_focus_label} 的直接匹配仍不够完整，建议面试继续核验。"
        )

    evidence = [
        f"关键链路：方法({method_signal_fragments})/产出({output_fragments})/结果({result_signal_fragments})/仅通用执行({generic_only_fragments})",
        f"完整度信号：时间({time_hits})/动作({action_hits})/结果({result_hits})/角色({role_hits})",
    ]

    if role_kind == "ai_pm":
        evidence.append(
            f"AI 产品支撑：{_bool_cn(has_ai_project_support)}；产品任务支撑：{_bool_cn(has_pm_project_support)}；PRD/原型：{_bool_cn(has_prd_or_prototype)}"
        )
    elif role_kind == "general_pm":
        evidence.append(
            f"产品任务支撑：{_bool_cn(has_pm_project_support)}；PRD/原型：{_bool_cn(has_prd_or_prototype)}；需求/用户/A-B/上线复盘命中：{_bool_cn(has_role_core_support)}"
        )
    elif role_kind == "data_analyst":
        evidence.append(
            f"数据分析支撑：{_bool_cn(has_data_project_support)}；SQL/指标/报表/实验：{_bool_cn(has_sql_or_metric_support)}；业务结论/洞察：{_bool_cn(has_result_signal)}"
        )
    elif role_kind == "user_research":
        evidence.append(
            f"研究任务支撑：{_bool_cn(has_research_project_support)}；访谈/问卷/可用性：{_bool_cn(has_research_method_support)}；市场活动替代风险：{'是' if market_activity_only else '否'}"
        )
    else:
        evidence.append(
            f"模板命中：核心任务({role_core_fragments})；方法关键词({method_hits + eval_hits + ai_hits})；产出关键词({output_hits})；结果关键词({result_signal_hits})"
        )

    ranked_candidates = sorted(
        fragment_candidates,
        key=lambda item: (item["rank"], len(item["keywords"]), len(item["raw"])),
        reverse=True,
    )
    for candidate in ranked_candidates[:2]:
        keyword_hint = f"；命中：{'、'.join(candidate['keywords'])}" if candidate["keywords"] else ""
        evidence.append(f"代表片段（{candidate['label']}）：{_snippet(candidate['raw'], max_len=60)}{keyword_hint}")

    return {
        "score": score,
        "reason": reason,
        "evidence": _top_evidence(evidence, limit=4),
        "meta": {
            "has_ai_or_method": has_ai_or_method,
            "has_data_project_support": has_data_project_support,
            "has_research_project_support": has_research_project_support,
            "has_pm_project_support": has_pm_project_support,
            "has_prd_or_prototype": has_prd_or_prototype,
            "market_activity_only": market_activity_only,
            "has_output_signal": has_output_signal,
            "has_result_signal": has_result_signal,
            "has_method_output_result": has_method_output_result,
            "experience_pattern": experience_pattern,
        },
    }


def _score_skills(parsed_jd: dict[str, Any], parsed_resume: dict[str, Any], role_profile: dict[str, Any]) -> ScoreDetail:
    required = parsed_jd.get("required_skills", []) or []
    bonus = parsed_jd.get("bonus_skills", []) or []
    resume_skills = parsed_resume.get("skills", []) or []
    role_focus = role_profile.get("skill_focus_keywords", []) or []
    required_alias_map = parsed_jd.get("required_skill_aliases_map") if isinstance(parsed_jd.get("required_skill_aliases_map"), dict) else {}
    bonus_alias_map = parsed_jd.get("bonus_skill_aliases_map") if isinstance(parsed_jd.get("bonus_skill_aliases_map"), dict) else {}

    required_hits: list[str] = []
    required_alias_hits: list[str] = []
    for req in required:
        matched, matched_term = _skill_group_match(req, resume_skills, required_alias_map)
        if matched:
            required_hits.append(req)
            if matched_term and _norm_skill(matched_term) != _norm_skill(req):
                required_alias_hits.append(f"{req}->{matched_term}")

    bonus_hits: list[str] = []
    bonus_alias_hits: list[str] = []
    for b in bonus:
        matched, matched_term = _skill_group_match(b, resume_skills, bonus_alias_map)
        if matched:
            bonus_hits.append(b)
            if matched_term and _norm_skill(matched_term) != _norm_skill(b):
                bonus_alias_hits.append(f"{b}->{matched_term}")

    role_focus_hits: list[str] = []
    for focus in role_focus:
        if any(_skill_match(focus, rs) for rs in resume_skills):
            role_focus_hits.append(focus)

    if required:
        hit_rate = len(required_hits) / len(required)
        if hit_rate >= 0.8:
            score = 5
        elif hit_rate >= 0.5:
            score = 4
        elif hit_rate >= 0.3:
            score = 3
        elif hit_rate > 0:
            score = 2
        else:
            score = 1
    else:
        score = 3 if len(resume_skills) >= 2 else 2

    # 模板重点技能（如 AI PM）命中可轻量加分
    if role_focus and len(role_focus_hits) >= 3 and score < 5:
        score += 1

    # bonus 仅轻量加分
    if bonus_hits and score < 5:
        score += 1

    score = _clip_score(score)
    has_sql = any(_skill_match("SQL", rs) for rs in resume_skills)
    if role_profile is DATA_ANALYST_PROFILE and not has_sql:
        score = min(score, 2)

    if role_profile is USER_RESEARCH_PROFILE:
        has_research_method_skill = any(
            any(_skill_match(k, rs) for rs in resume_skills)
            for k in ["用户访谈", "问卷设计", "可用性测试", "定性研究", "定量研究"]
        )
        if not has_research_method_skill:
            score = min(score, 3)

    if score >= 4:
        reason = "核心技能覆盖较好，具备推进岗位工作的基础能力。"
    else:
        reason = "技能证据仍有缺口，建议重点追问 AI 产品工具链与方法论实操。"
    if role_profile is DATA_ANALYST_PROFILE and score <= 3:
        reason = "数据岗位关键技能仍有缺口，建议重点追问 SQL、指标体系与分析落地能力。"
    if role_profile is USER_RESEARCH_PROFILE and score <= 3:
        reason = "研究岗位方法技能证据不足，建议重点追问访谈、问卷与可用性测试实操。"
    if role_profile is GENERAL_PM_PROFILE and score <= 3:
        reason = "产品岗位关键方法技能证据不足，建议重点核验需求分析、PRD与原型能力。"

    evidence = [
        f"JD 必备技能命中：{len(required_hits)}/{len(required) if required else 0}",
        f"命中必备技能：{required_hits if required_hits else '无'}",
        f"命中加分技能：{bonus_hits if bonus_hits else '无'}",
        f"模板重点技能命中：{role_focus_hits if role_focus_hits else '无'}",
        f"SQL 证据：{'有' if has_sql else '无'}",
    ]

    if required_alias_hits or bonus_alias_hits:
        evidence.append("RAG synonym expansion hits: " + " | ".join(required_alias_hits + bonus_alias_hits))

    return {"score": score, "reason": reason, "evidence": _top_evidence(evidence)}


def _score_expression(parsed_resume: dict[str, Any]) -> ScoreDetail:
    education_ok = bool(parsed_resume.get("education"))
    experience_ok = bool(parsed_resume.get("internships") or parsed_resume.get("projects"))
    skills_ok = bool(parsed_resume.get("skills"))

    fragments = (parsed_resume.get("internships") or []) + (parsed_resume.get("projects") or [])
    time_hits = sum(1 for f in fragments if f.get("time_found"))
    action_hits = sum(1 for f in fragments if f.get("action_keywords"))
    result_hits = sum(1 for f in fragments if f.get("result_keywords"))

    present_cnt = sum([education_ok, experience_ok, skills_ok])
    score = 2 if present_cnt <= 1 else 3 if present_cnt == 2 else 4
    if time_hits >= 1 and action_hits >= 1 and result_hits >= 1 and score < 5:
        score += 1

    score = _clip_score(score)
    evidence = [
        f"结构完整度：教育({education_ok})/经历({experience_ok})/技能({skills_ok})",
        f"经历片段信号：时间({time_hits})/动作({action_hits})/结果({result_hits})",
    ]
    if fragments:
        evidence.append(f"原文片段：{_snippet(fragments[0].get('raw_text', ''))}")

    reason = "简历结构和表达完整度较好。" if score >= 4 else "表达完整度一般，存在信息缺口，建议补充核验。"
    return {"score": score, "reason": reason, "evidence": _top_evidence(evidence)}


def _score_overall(
    score_map: dict[str, int],
    details: DetailedScores,
    role_profile: dict[str, Any],
    resolved_cfg: dict[str, Any] | None = None,
) -> ScoreDetail:
    # 基础分：使用均分做底座，但增加硬约束上限
    cfg = resolved_cfg or {}
    weights = cfg.get("weights") if isinstance(cfg.get("weights"), dict) else None
    if not weights:
        weights = role_profile.get("weights") if role_profile.get("weights") else {k: 1.0 for k in score_map.keys()}

    weighted_sum = sum(score_map[k] * float(weights.get(k, 1.0)) for k in score_map.keys())
    weight_total = sum(float(weights.get(k, 1.0)) for k in score_map.keys())
    avg = round(weighted_sum / weight_total) if weight_total else round(sum(score_map.values()) / len(score_map))
    score = _clip_score(avg)

    exp_score = score_map.get("相关经历匹配度", 1)
    skill_score = score_map.get("技能匹配度", 1)
    low_count = sum(1 for v in score_map.values() if v <= 2)

    # 约束 1：经历或技能过低 -> 综合推荐度上限 3
    if exp_score <= 2 or skill_score <= 2:
        score = min(score, 3)
    # 约束 2：前四项有两项及以上 <=2 -> 上限 2
    if low_count >= 2:
        score = min(score, 2)
    # 约束 3：只有经历和技能都较强时才允许到 5
    if not (exp_score >= 4 and skill_score >= 4):
        score = min(score, 4)

    strengths = [k for k, v in score_map.items() if v >= 4]
    evidence = [
        f"前四项分数：{score_map}",
        f"弱项数量(<=2)：{low_count}，经历={exp_score}，技能={skill_score}",
        f"优势维度：{strengths if strengths else '暂无明显优势'}",
        f"岗位模板：{role_profile.get('profile_name', '通用岗位模板')}",
    ]

    if role_profile is AI_PM_PROFILE:
        exp_meta = (details.get("相关经历匹配度") or {}).get("meta") or {}
        if not exp_meta.get("has_ai_or_method", False):
            score = min(score, 3)
    if role_profile is DATA_ANALYST_PROFILE:
        skill_detail = details.get("技能匹配度") or {}
        skill_evidence = " ".join(skill_detail.get("evidence") or [])
        exp_meta = (details.get("相关经历匹配度") or {}).get("meta") or {}
        if "SQL 证据：无" in skill_evidence:
            score = min(score, 3)
            evidence.append("硬门槛触发：缺少 SQL 证据，综合推荐上限下调。")
        if not exp_meta.get("has_data_project_support", False):
            score = min(score, 3)
            evidence.append("硬门槛触发：缺少数据项目/实习支撑，综合推荐上限下调。")
    if role_profile is USER_RESEARCH_PROFILE:
        exp_meta = (details.get("相关经历匹配度") or {}).get("meta") or {}
        if not exp_meta.get("has_research_project_support", False):
            score = min(score, 3)
            evidence.append("硬门槛触发：缺少研究项目支撑，综合推荐上限下调。")
        if exp_meta.get("market_activity_only", False):
            score = min(score, 3)
            evidence.append("硬门槛触发：市场活动经验不能直接等同用户研究经验。")

    if low_count >= 2:
        reason = "前四项中低分维度较多，综合推荐度需严格受限。"
    elif exp_score <= 2 or skill_score <= 2:
        reason = "经历或技能存在关键短板，综合推荐度上限下调。"
    elif score >= 4:
        reason = "前四项整体较强，综合匹配度较高。"
    else:
        reason = "综合匹配度中等，建议人工复核后决策。"

    return {"score": _clip_score(score), "reason": reason, "evidence": _top_evidence(evidence)}


def score_candidate(parsed_jd: dict[str, Any], parsed_resume: dict[str, Any]) -> DetailedScores:
    """主函数：输出五个评分维度（含 score/reason/evidence）。"""
    raw_cfg = parsed_jd.get("scoring_config") if isinstance(parsed_jd.get("scoring_config"), dict) else {}

    template_name = raw_cfg.get("role_template") or raw_cfg.get("profile_name")
    detected_profile = detect_role_profile(parsed_jd)
    profile_from_template = get_profile_by_name(template_name) if template_name else DEFAULT_PROFILE
    base_profile = profile_from_template if template_name else detected_profile

    resolved_cfg, has_custom_config = merge_scoring_config(base_profile, raw_cfg)
    role_profile = get_profile_by_name(resolved_cfg.get("role_template") or base_profile.get("profile_name"))

    education_detail = _score_education(parsed_jd, parsed_resume)
    experience_detail = _score_experience(parsed_resume, role_profile)
    skills_detail = _score_skills(parsed_jd, parsed_resume, role_profile)
    expression_detail = _score_expression(parsed_resume)

    base_scores = {
        "教育背景匹配度": education_detail["score"],
        "相关经历匹配度": experience_detail["score"],
        "技能匹配度": skills_detail["score"],
        "表达完整度": expression_detail["score"],
    }

    details: DetailedScores = {
        "教育背景匹配度": education_detail,
        "相关经历匹配度": experience_detail,
        "技能匹配度": skills_detail,
        "表达完整度": expression_detail,
    }

    details["综合推荐度"] = _score_overall(base_scores, details, role_profile, resolved_cfg)

    thresholds = resolved_cfg.get("screening_thresholds", {}) if isinstance(resolved_cfg, dict) else {}
    min_exp = int(thresholds.get("min_experience", 1) or 1)
    min_skill = int(thresholds.get("min_skill", 1) or 1)
    min_expr = int(thresholds.get("min_expression", 1) or 1)
    if base_scores["相关经历匹配度"] < min_exp or base_scores["技能匹配度"] < min_skill or base_scores["表达完整度"] < min_expr:
        details["综合推荐度"]["score"] = min(details["综合推荐度"]["score"], 3)
        details["综合推荐度"].setdefault("evidence", []).append("岗位最低分门槛触发：综合推荐上限下调。")

    hard_thresholds = resolved_cfg.get("hard_thresholds", {}) if isinstance(resolved_cfg, dict) else {}
    if hard_thresholds.get("require_sql"):
        skill_ev = " ".join((details.get("技能匹配度") or {}).get("evidence", []))
        if "SQL 证据：无" in skill_ev:
            details["综合推荐度"]["score"] = min(details["综合推荐度"]["score"], 3)
            details["综合推荐度"].setdefault("evidence", []).append("硬门槛触发：缺少 SQL 证据。")
    if hard_thresholds.get("require_data_project_support"):
        exp_meta = (details.get("相关经历匹配度") or {}).get("meta") or {}
        if not exp_meta.get("has_data_project_support", False):
            details["综合推荐度"]["score"] = min(details["综合推荐度"]["score"], 3)
            details["综合推荐度"].setdefault("evidence", []).append("硬门槛触发：缺少数据项目/实习支撑。")
    if hard_thresholds.get("require_research_project_support"):
        exp_meta = (details.get("相关经历匹配度") or {}).get("meta") or {}
        if not exp_meta.get("has_research_project_support", False):
            details["综合推荐度"]["score"] = min(details["综合推荐度"]["score"], 3)
            details["综合推荐度"].setdefault("evidence", []).append("硬门槛触发：缺少研究项目支撑。")
    if hard_thresholds.get("market_activity_not_equal_research"):
        exp_meta = (details.get("相关经历匹配度") or {}).get("meta") or {}
        if exp_meta.get("market_activity_only", False):
            details["综合推荐度"]["score"] = min(details["综合推荐度"]["score"], 3)
            details["综合推荐度"].setdefault("evidence", []).append("硬门槛触发：市场活动经验不能直接等同用户研究经验。")
    if hard_thresholds.get("require_pm_project_support"):
        exp_meta = (details.get("相关经历匹配度") or {}).get("meta") or {}
        if not exp_meta.get("has_pm_project_support", False):
            details["综合推荐度"]["score"] = min(details["综合推荐度"]["score"], 3)
            details["综合推荐度"].setdefault("evidence", []).append("硬门槛触发：缺少产品项目/实习支撑。")
    if hard_thresholds.get("require_prd_or_prototype"):
        exp_meta = (details.get("相关经历匹配度") or {}).get("meta") or {}
        if not exp_meta.get("has_prd_or_prototype", False):
            details["综合推荐度"]["score"] = min(details["综合推荐度"]["score"], 3)
            details["综合推荐度"].setdefault("evidence", []).append("硬门槛触发：缺少 PRD/原型交付证据。")

    if resolved_cfg.get("risk_focus"):
        details["综合推荐度"].setdefault("evidence", []).append(
            f"岗位风险关注点：{', '.join(resolved_cfg.get('risk_focus')[:3])}"
        )
    details["综合推荐度"].setdefault("evidence", []).append(
        f"岗位评分模板：{role_profile.get('profile_name', '通用岗位模板')}"
    )
    details["综合推荐度"].setdefault("evidence", []).append(
        f"岗位自定义评分配置：{'已启用' if has_custom_config else '未启用（使用模板默认）'}"
    )
    hydrate_representative_evidence(details)
    return details


def to_score_values(details: DetailedScores) -> dict[str, int]:
    """把详细评分转换为纯分数字典，便于 risk/screener 复用。"""
    return {k: int(v.get("score", 1)) for k, v in details.items()}


if __name__ == "__main__":
    from pprint import pprint

    demo_jd = {
        "job_title": "AI 产品经理实习生",
        "degree_requirement": "本科及以上",
        "major_preference": "计算机/数据相关专业",
        "required_skills": ["SQL", "Python", "PRD", "数据分析", "A/B 测试"],
        "bonus_skills": ["大模型项目经验", "英文文档"],
        "internship_requirement": "每周4天，3个月",
        "competency_requirements": ["产品思维", "结构化思维"],
        "scoring_config": {
            "role_template": "AI产品经理 / 大模型产品经理",
            "weights": {"教育背景匹配度": 0.2, "相关经历匹配度": 0.35, "技能匹配度": 0.3, "表达完整度": 0.15},
            "screening_thresholds": {"min_experience": 2, "min_skill": 2, "min_expression": 2},
            "hard_thresholds": {},
        },
    }

    demo_resume = {
        "name": "张三",
        "education": "某大学 市场营销 本科",
        "degree": "本科",
        "major": "市场营销",
        "graduation_date": "2026年6月",
        "internships": [
            {
                "raw_text": "2025.10-至今 参与AI产品小项目，主导需求拆解并推动上线，转化提升8%",
                "time_found": True,
                "action_keywords": ["主导", "推动", "上线", "拆解"],
                "result_keywords": ["提升", "%", "转化"],
                "role_keywords": ["项目负责人"],
            }
        ],
        "projects": [],
        "skills": ["SQL", "Python", "PRD", "A/B测试", "Prompt"],
        "awards": [],
        "languages": ["英语"],
    }

    detailed = score_candidate(demo_jd, demo_resume)
    pprint(detailed)
    print(to_score_values(detailed))
