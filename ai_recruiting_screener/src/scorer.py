"""评分模块（规则版，含证据解释）。

模块职责：
- 基于结构化 JD + 结构化简历输出五维评分。
- 每个维度输出 score / reason / evidence，便于解释与追溯。
- 提供分数提取函数，方便 screener.py / risk_analyzer.py 使用。
"""

from __future__ import annotations

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


def _clip_score(v: int) -> int:
    return max(1, min(5, v))


def _top_evidence(items: list[str], limit: int = 3) -> list[str]:
    return items[:limit] if items else ["未发现强证据，建议面试补充核验。"]


def _snippet(text: str, max_len: int = 45) -> str:
    raw = (text or "").strip()
    return raw if len(raw) <= max_len else raw[: max_len - 1] + "…"


def _norm_skill(s: str) -> str:
    return (s or "").lower().replace(" ", "")


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

    completeness_score = 1
    if len(fragments) >= 1:
        completeness_score += 1
    if time_hits >= 1 and action_hits >= 1:
        completeness_score += 1
    if result_hits >= 1 or role_hits >= 1:
        completeness_score += 1

    # 层次 2：岗位相关性（模板化）
    generic_action_keywords = ["负责", "推动", "协调", "落地", "分析"]
    method_keywords = role_profile.get("experience_method_keywords") or []
    ai_product_keywords = role_profile.get("experience_ai_keywords") or []
    eval_keywords = role_profile.get("experience_eval_keywords") or []

    ai_hits = 0
    method_hits = 0
    eval_hits = 0
    generic_hits = 0
    ai_project_or_intern_hits = 0
    quote_lines: list[str] = []

    for f in fragments:
        raw = (f.get("raw_text") or "")
        raw_norm = raw.lower().replace(" ", "")

        matched_ai = any(k.replace(" ", "") in raw_norm for k in ai_product_keywords)
        matched_method = any(k.replace(" ", "") in raw_norm for k in method_keywords)
        matched_eval = any(k.replace(" ", "") in raw_norm for k in eval_keywords)
        matched_generic = any(k.replace(" ", "") in raw_norm for k in generic_action_keywords)
        matched_ai_project_or_intern = matched_ai and any(k in raw_norm for k in ["项目", "实习", "产品"])

        if matched_ai:
            ai_hits += 1
        if matched_method:
            method_hits += 1
        if matched_eval:
            eval_hits += 1
        if matched_generic:
            generic_hits += 1
        if matched_ai_project_or_intern:
            ai_project_or_intern_hits += 1

        if (matched_method or matched_ai or matched_eval or matched_generic) and len(quote_lines) < 3:
            quote_lines.append(_snippet(raw))

    # 相关性分数：AI/产品方法/评估信号主导，通用动作词仅弱加分
    relevance_score = 0
    if ai_hits >= 1:
        relevance_score += 2
    if method_hits >= 2:
        relevance_score += 2
    elif method_hits == 1:
        relevance_score += 1
    if eval_hits >= 1:
        relevance_score += 1
    if ai_project_or_intern_hits >= 1:
        relevance_score += 1
    if generic_hits >= 1:
        relevance_score += 1  # 弱加分

    raw_score = completeness_score + relevance_score

    # 约束：若只有通用动作词、没有 AI / 产品方法证据，上限不高
    has_ai_or_method = (ai_hits + method_hits + eval_hits) > 0
    if not has_ai_or_method and generic_hits > 0:
        score = min(_clip_score(raw_score), int(role_profile.get("hard_cap_when_generic_only") or 3))
    else:
        score = _clip_score(raw_score)

    if role_profile is DATA_ANALYST_PROFILE:
        if has_ai_or_method and score >= 4:
            reason = "候选人具备较完整的数据分析项目证据，能体现指标分析与业务洞察能力。"
        elif not has_ai_or_method and generic_hits > 0:
            reason = "经历以通用执行描述为主，缺少数据分析方法与项目证据，建议重点核验。"
        else:
            reason = "具备部分数据分析经历，但业务结论与项目支撑仍需面试进一步确认。"
    elif has_ai_or_method and score >= 4:
        reason = "候选人在 AI 产品项目中展示了较完整的方法与落地证据，岗位相关性较好。"
    elif not has_ai_or_method and generic_hits > 0:
        reason = "经历中以通用执行动作为主，AI 产品方法与关键证据偏弱，建议重点核验。"
    else:
        reason = "经历与岗位存在一定匹配，但关键 AI 产品证据仍需在面试中进一步确认。"

    has_data_project_support = method_hits > 0 and any(
        kw in (f.get("raw_text") or "").lower().replace(" ", "") for f in fragments for kw in ["数据", "指标", "sql", "python", "报表", "可视化"]
    )
    has_research_project_support = method_hits > 0 and any(
        kw in (f.get("raw_text") or "").lower().replace(" ", "")
        for f in fragments
        for kw in ["研究", "访谈", "问卷", "可用性", "洞察", "报告"]
    )
    has_pm_project_support = method_hits > 0 and any(
        kw in (f.get("raw_text") or "").lower().replace(" ", "")
        for f in fragments
        for kw in ["产品", "需求", "prd", "原型", "竞品", "用户"]
    )
    has_prd_or_prototype = any(
        kw in (f.get("raw_text") or "").lower().replace(" ", "")
        for f in fragments
        for kw in ["prd", "需求文档", "原型", "axure", "figma"]
    )
    market_activity_only = all(
        any(k in (f.get("raw_text") or "").lower().replace(" ", "") for k in ["活动", "运营", "拉新", "投放", "传播"])
        and not any(k in (f.get("raw_text") or "").lower().replace(" ", "") for k in ["访谈", "问卷", "可用性", "洞察", "研究"])
        for f in fragments
    ) if fragments else False

    if role_profile is GENERAL_PM_PROFILE:
        if has_ai_or_method and has_pm_project_support and score >= 4:
            reason = "候选人具备较完整的产品方法与项目实践证据，能够支撑通用产品岗位推进。"
        elif not has_pm_project_support:
            reason = "产品项目或实习支撑不足，建议重点核验需求拆解与方案落地能力。"
            score = min(score, 3)
        elif not has_prd_or_prototype:
            reason = "经历中缺少 PRD/原型等关键交付证据，建议重点追问方法论实操。"
            score = min(score, 3)
        else:
            reason = "具备部分产品岗位相关经历，但关键交付证据仍需在面试中进一步确认。"

    if role_profile is USER_RESEARCH_PROFILE:
        if has_ai_or_method and score >= 4:
            reason = "候选人呈现了较完整的研究方法与研究产出证据，岗位匹配度较好。"
        elif market_activity_only:
            reason = "经历偏市场活动执行，尚不足以直接代表用户研究能力，建议重点核验研究方法。"
            score = min(score, 3)
        else:
            reason = "研究经历存在，但方法与可验证产出证据仍需进一步确认。"

    evidence = [
        f"完整度信号：时间({time_hits})/动作({action_hits})/结果({result_hits})/角色({role_hits})",
        f"岗位相关命中：AI词({ai_hits})/产品方法词({method_hits})/评估优化词({eval_hits})/通用动作词({generic_hits})",
        f"AI 产品项目/实习证据命中：{ai_project_or_intern_hits}",
        f"研究项目支撑：{'有' if has_research_project_support else '无'}；市场活动替代风险：{'是' if market_activity_only else '否'}",
        f"产品项目支撑：{'有' if has_pm_project_support else '无'}；PRD/原型证据：{'有' if has_prd_or_prototype else '无'}",
    ]
    for q in quote_lines:
        evidence.append(f"原文片段：{q}")

    return {
        "score": score,
        "reason": reason,
        "evidence": _top_evidence(evidence),
        "meta": {
            "has_ai_or_method": has_ai_or_method,
            "has_data_project_support": has_data_project_support,
            "has_research_project_support": has_research_project_support,
            "has_pm_project_support": has_pm_project_support,
            "has_prd_or_prototype": has_prd_or_prototype,
            "market_activity_only": market_activity_only,
        },
    }


def _score_skills(parsed_jd: dict[str, Any], parsed_resume: dict[str, Any], role_profile: dict[str, Any]) -> ScoreDetail:
    required = parsed_jd.get("required_skills", []) or []
    bonus = parsed_jd.get("bonus_skills", []) or []
    resume_skills = parsed_resume.get("skills", []) or []
    role_focus = role_profile.get("skill_focus_keywords", []) or []

    required_hits: list[str] = []
    for req in required:
        if any(_skill_match(req, rs) for rs in resume_skills):
            required_hits.append(req)

    bonus_hits: list[str] = []
    for b in bonus:
        if any(_skill_match(b, rs) for rs in resume_skills):
            bonus_hits.append(b)

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
