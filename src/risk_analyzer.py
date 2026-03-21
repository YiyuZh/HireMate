"""风险点分析模块（规则版）。

目标：
- 识别候选人简历中的高/中/低风险。
- 输出标准化风险结果，供 screener.py 使用。

输入：
1) 结构化简历数据（优先）
2) 评分结果或纯分数字典（可选）
3) 原始简历文本（可选）

输出：
- risk_level: high / medium / low
- risk_points: 风险点列表
- risk_summary: 风险总结
"""

from __future__ import annotations

from typing import Any


def _ensure_score_values(scores_input: dict[str, Any] | None) -> dict[str, int]:
    """兼容详细评分和纯分数字典。"""
    if not scores_input:
        return {}

    sample = next(iter(scores_input.values()))
    if isinstance(sample, dict) and "score" in sample:
        return {k: int(v.get("score", 1)) for k, v in scores_input.items()}

    return {k: int(v) for k, v in scores_input.items()}


def _timeline_unclear(fragments: list[dict[str, Any]]) -> bool:
    """时间线不清晰：经历存在但时间信号命中偏低。"""
    if not fragments:
        return True
    time_hits = sum(1 for f in fragments if f.get("time_found"))
    return time_hits == 0


def _support_for_skills(resume_data: dict[str, Any]) -> bool:
    """技能是否有项目/实习支撑：至少一个片段有动作或结果词。"""
    fragments = (resume_data.get("internships") or []) + (resume_data.get("projects") or [])
    if not fragments:
        return False
    for f in fragments:
        if f.get("action_keywords") or f.get("result_keywords"):
            return True
    return False


def _vague_experience_ratio(resume_data: dict[str, Any]) -> float:
    """经历空泛度：无动作且无结果的片段占比。"""
    fragments = (resume_data.get("internships") or []) + (resume_data.get("projects") or [])
    if not fragments:
        return 1.0
    vague = 0
    for f in fragments:
        has_action = bool(f.get("action_keywords"))
        has_result = bool(f.get("result_keywords"))
        if not has_action and not has_result:
            vague += 1
    return vague / len(fragments)


def _is_data_analyst_context(resume_data: dict[str, Any]) -> bool:
    """基于简历技能与经历信号判断是否为数据分析岗位语境。"""
    skills = " ".join(resume_data.get("skills") or []).lower().replace(" ", "")
    fragments = (resume_data.get("internships") or []) + (resume_data.get("projects") or [])
    raw_blob = " ".join((f.get("raw_text") or "") for f in fragments).lower().replace(" ", "")
    text = f"{skills} {raw_blob}"
    data_signals = ["sql", "python", "数据分析", "指标", "报表", "可视化", "ab测试", "a/b测试"]
    research_signals = ["用户访谈", "问卷", "可用性", "研究报告", "定性研究", "定量研究", "洞察"]
    data_hit = sum(1 for sig in data_signals if sig in text)
    research_hit = sum(1 for sig in research_signals if sig in text)
    # 避免把“用户研究”简历误判为“数据分析”语境：至少两个数据信号，且研究信号不更强
    return data_hit >= 2 and data_hit >= research_hit


def _is_user_research_context(resume_data: dict[str, Any]) -> bool:
    skills = " ".join(resume_data.get("skills") or []).lower().replace(" ", "")
    fragments = (resume_data.get("internships") or []) + (resume_data.get("projects") or [])
    raw_blob = " ".join((f.get("raw_text") or "") for f in fragments).lower().replace(" ", "")
    text = f"{skills} {raw_blob}"
    signals = ["用户访谈", "问卷", "可用性", "研究报告", "定性研究", "定量研究", "洞察", "研究项目"]
    return ("用户研究" in text) or (sum(1 for sig in signals if sig in text) >= 2)


def analyze_risk(
    resume_data: dict[str, Any],
    scores_input: dict[str, Any] | None = None,
    resume_text: str | None = None,
) -> dict[str, Any]:
    """输出标准化风险结果。

    规则参考：docs/risk_levels.md + scoring_rules.md。
    """
    score_values = _ensure_score_values(scores_input)

    exp_score = score_values.get("相关经历匹配度")
    skill_score = score_values.get("技能匹配度")
    expression_score = score_values.get("表达完整度")

    has_resume_struct = bool(resume_data)
    fragments = (resume_data.get("internships") or []) + (resume_data.get("projects") or [])
    skills = resume_data.get("skills") or []

    risk_points: list[str] = []
    high_flags = 0
    medium_flags = 0

    # 1) 关键事实缺失
    key_missing = not resume_data.get("education") or not resume_data.get("degree") or not fragments
    # 仅在确实传入结构化简历时，才把字段缺失作为强风险信号
    if has_resume_struct and key_missing:
        risk_points.append("关键信息存在缺失（教育/经历字段不完整），建议人工补充核验。")
        high_flags += 1

    # 2) 时间线不清晰
    # 若未传结构化简历，不对时间线做强判定，避免误伤
    if has_resume_struct and _timeline_unclear(fragments):
        risk_points.append("经历时间线不清晰，难以判断持续时长与投入深度。")
        medium_flags += 1

    # 3) 技能缺少项目或实习支撑
    if skills and not _support_for_skills(resume_data):
        risk_points.append("技能有罗列但缺少项目/实习中的应用证据，建议面试重点追问。")
        medium_flags += 1

    # 4) 经历描述过于空泛
    vague_ratio = _vague_experience_ratio(resume_data) if has_resume_struct else 0.0
    if has_resume_struct and vague_ratio >= 0.6:
        risk_points.append("经历描述偏空泛，缺少动作与结果细节，证据强度有限。")
        medium_flags += 1

    # 4.1) 数据岗补充风险
    if _is_data_analyst_context(resume_data):
        fragments_text = " ".join((f.get("raw_text") or "") for f in fragments).lower().replace(" ", "")
        has_metric_signal = any(k in fragments_text for k in ["指标", "漏斗", "转化", "留存", "同比", "环比", "口径"])
        has_result_signal = any(k in fragments_text for k in ["提升", "下降", "%", "结论", "洞察", "建议"])
        if skills and not _support_for_skills(resume_data):
            risk_points.append("技能缺少项目支撑：技能有罗列，但缺少可验证的数据项目/实习证据。")
            medium_flags += 1
        if not has_metric_signal:
            risk_points.append("指标分析经验不足：缺少指标体系、口径或关键指标分析证据。")
            medium_flags += 1
        if not has_result_signal or vague_ratio >= 0.6:
            risk_points.append("数据结论表达空泛：有分析过程但业务结论与可落地建议不足。")
            medium_flags += 1

    # 4.2) 用户研究岗补充风险
    if _is_user_research_context(resume_data):
        fragments_text = " ".join((f.get("raw_text") or "") for f in fragments).lower().replace(" ", "")
        has_method_signal = any(k in fragments_text for k in ["访谈", "问卷", "可用性", "样本", "研究方法", "定性", "定量"])
        has_output_signal = any(k in fragments_text for k in ["洞察", "结论", "研究报告", "建议", "发现"])
        if not has_method_signal:
            risk_points.append("方法论证据不足：缺少访谈/问卷/可用性测试等研究方法证据。")
            medium_flags += 1
        if vague_ratio >= 0.6:
            risk_points.append("研究经历过于空泛：描述偏执行层，缺少研究过程与关键发现。")
            medium_flags += 1
        if not has_output_signal:
            risk_points.append("缺少可验证研究产出：未体现洞察结论、研究报告或可落地建议。")
            medium_flags += 1

    # 5) 与岗位关联度偏弱（优先参考评分）
    if exp_score is not None and exp_score <= 2:
        risk_points.append("相关经历匹配度较低，与 AI 产品经理实习岗位关联偏弱。")
        high_flags += 1
    elif exp_score == 3:
        risk_points.append("相关经历匹配度一般，建议在面试中核验岗位相关职责。")
        medium_flags += 1

    if skill_score is not None and skill_score <= 2:
        risk_points.append("技能匹配度偏低，关键能力与岗位要求存在差距。")
        high_flags += 1

    if expression_score is not None and expression_score == 1:
        risk_points.append("表达完整度较低，当前信息不足以稳定判断。")
        high_flags += 1

    # 原始文本辅助（可选）
    if resume_text is not None and len((resume_text or "").strip()) < 120:
        risk_points.append("简历文本长度较短，可能遗漏关键事实。")
        medium_flags += 1

    # 风险分级（对齐 docs/risk_levels.md）
    if high_flags >= 1:
        risk_level = "high"
        risk_summary = "存在高风险项：当前证据不足以直接推荐进入下一轮，建议优先人工复核或暂不推荐。"
    elif medium_flags >= 1:
        risk_level = "medium"
        risk_summary = "存在中风险项：建议人工复核，并在面试中围绕风险点做补充验证。"
    else:
        risk_level = "low"
        risk_summary = "整体风险较低：当前信息较完整，可按常规流程进入下一步核验。"
        risk_points = ["未识别到明显高/中风险项，建议按常规面试流程核验关键事实。"]

    return {
        "risk_level": risk_level,
        "risk_points": risk_points[:4],
        "risk_summary": risk_summary,
    }


def detect_risks(scores: dict[str, int], resume_text: str, resume_data: dict[str, Any] | None = None) -> list[str]:
    """兼容旧接口：返回风险点列表（供旧流程使用）。"""
    result = analyze_risk(resume_data=(resume_data or {}), scores_input=scores, resume_text=resume_text)
    return result["risk_points"]


if __name__ == "__main__":
    # 本地测试示例：
    # cd HireMate
    # python src/risk_analyzer.py
    demo_resume = {
        "education": "某大学 本科",
        "degree": "本科",
        "internships": [
            {
                "raw_text": "2025.06-2025.09 参与项目推进",
                "time_found": True,
                "action_keywords": ["推进"],
                "result_keywords": [],
                "role_keywords": ["实习生"],
            }
        ],
        "projects": [],
        "skills": ["SQL", "Python"],
    }
    demo_scores = {
        "教育背景匹配度": 4,
        "相关经历匹配度": 3,
        "技能匹配度": 3,
        "表达完整度": 3,
        "综合推荐度": 3,
    }

    print(analyze_risk(demo_resume, demo_scores, "示例简历文本"))
