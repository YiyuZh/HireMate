"""面试建议模块（规则版）。

目标：
- 根据 JD、简历、评分、风险和初筛结论，生成可直接给 HR/面试官使用的面试建议。

输出：
- interview_questions: 3-5 个建议追问问题
- focus_points: 2-4 个重点核实能力点
- interview_summary: 一段简洁总结
"""

from __future__ import annotations

from typing import Any


def _ensure_score_values(scores_input: dict[str, Any]) -> dict[str, int]:
    """兼容详细评分和纯分数字典。"""
    if not scores_input:
        return {}
    sample = next(iter(scores_input.values()))
    if isinstance(sample, dict) and "score" in sample:
        return {k: int(v.get("score", 1)) for k, v in scores_input.items()}
    return {k: int(v) for k, v in scores_input.items()}


def _get_evidence_snippets(resume_data: dict[str, Any], limit: int = 2) -> list[str]:
    """从结构化经历中提取可追问片段。"""
    fragments = (resume_data.get("internships") or []) + (resume_data.get("projects") or [])
    snippets: list[str] = []
    for f in fragments:
        raw = (f.get("raw_text") or "").strip()
        if raw:
            snippets.append(raw if len(raw) <= 55 else raw[:54] + "…")
        if len(snippets) >= limit:
            break
    return snippets


def build_interview_plan(
    parsed_jd: dict[str, Any],
    parsed_resume: dict[str, Any],
    scores_input: dict[str, Any],
    risk_result: dict[str, Any],
    screening_result: str,
) -> dict[str, Any]:
    """生成结构化面试建议（规则版）。"""
    score_values = _ensure_score_values(scores_input)

    exp_score = score_values.get("相关经历匹配度", 1)
    skill_score = score_values.get("技能匹配度", 1)
    edu_score = score_values.get("教育背景匹配度", 1)

    required_skills = parsed_jd.get("required_skills", []) or []
    resume_skills = parsed_resume.get("skills", []) or []
    risk_points = risk_result.get("risk_points", []) or []
    risk_level = risk_result.get("risk_level", "")

    evidence_snippets = _get_evidence_snippets(parsed_resume)

    interview_questions: list[str] = []

    # 1) 项目真实职责（优先）
    interview_questions.append(
        "请你选一段最相关的实习/项目经历，按“背景-目标-你的职责-结果”完整说明，并明确你个人负责的部分。"
    )

    # 2) 产品思维
    interview_questions.append(
        "如果要把该经历中的方案再优化一次，你会如何定义核心用户问题、优先级和版本范围？"
    )

    # 3) 数据分析能力
    if any(s in required_skills for s in ["SQL", "Python", "数据分析", "A/B 测试", "A/B测试"]):
        interview_questions.append(
            "请结合你的项目说明一次你如何定义指标、做数据分析，并基于结果推动产品决策。"
        )
    else:
        interview_questions.append("请举例说明你如何用数据验证产品判断，而不是只凭直觉。")

    # 4) AI / 大模型理解
    if any(s in required_skills for s in ["LLM", "大模型", "Prompt"]):
        interview_questions.append(
            "你在 AI/大模型相关任务中做过哪些具体工作？如何评估效果并处理模型输出不稳定的问题？"
        )
    else:
        interview_questions.append("你如何理解 AI 产品经理与传统产品经理在问题定义和验证方式上的差异？")

    # 5) 风险点核验（按风险结果补充）
    if risk_points:
        interview_questions.append(f"针对风险点“{risk_points[0]}”，请补充可验证事实（时间、角色、产出）。")

    # 去重 + 限制 3-5
    deduped_questions: list[str] = []
    for q in interview_questions:
        if q not in deduped_questions:
            deduped_questions.append(q)
    interview_questions = deduped_questions[:5]
    if len(interview_questions) < 3:
        interview_questions.append("请补充一个你独立推动并落地的案例，重点说明你做出的关键判断。")

    # focus points（2-4）
    focus_points: list[str] = []
    focus_points.append("项目真实职责与个人贡献边界")
    focus_points.append("产品思维（问题定义、优先级、方案取舍）")

    if skill_score <= 3 or any(s in required_skills for s in ["SQL", "Python", "数据分析"]):
        focus_points.append("数据分析与指标驱动决策能力")
    if any(s in required_skills for s in ["LLM", "大模型", "Prompt"]):
        focus_points.append("AI/大模型应用理解与评估思路")

    # 若风险较高，强调核验
    if risk_level in {"high", "medium"}:
        focus_points.append("风险点事实核验（时间线、证据、结果真实性）")

    # 去重后截断 2-4
    uniq_focus: list[str] = []
    for fp in focus_points:
        if fp not in uniq_focus:
            uniq_focus.append(fp)
    focus_points = uniq_focus[:4]

    # 面试总结
    if screening_result == "推荐进入下一轮":
        summary_prefix = "候选人整体匹配度较好，建议以能力深挖为主。"
    elif screening_result == "建议人工复核":
        summary_prefix = "候选人存在待核验点，建议结构化追问后再决策。"
    else:
        summary_prefix = "候选人当前证据偏弱，建议聚焦关键短板核验。"

    summary_parts = [
        summary_prefix,
        f"当前分数：经历 {exp_score}/5，技能 {skill_score}/5，教育 {edu_score}/5。",
    ]
    if evidence_snippets:
        summary_parts.append(f"可优先围绕这些经历提问：{'; '.join(evidence_snippets)}。")
    if risk_points:
        summary_parts.append(f"重点风险：{risk_points[0]}。")

    interview_summary = " ".join(summary_parts)

    return {
        "interview_questions": interview_questions,
        "focus_points": focus_points,
        "interview_summary": interview_summary,
    }


def build_interview_questions(scores: dict[str, int], risks: list[str]) -> list[str]:
    """兼容旧接口：仅返回问题列表。"""
    plan = build_interview_plan(
        parsed_jd={},
        parsed_resume={},
        scores_input=scores,
        risk_result={"risk_level": "medium" if risks else "low", "risk_points": risks},
        screening_result="建议人工复核" if risks else "推荐进入下一轮",
    )
    return plan["interview_questions"]


if __name__ == "__main__":
    # 本地测试示例：
    # cd HireMate
    # python src/interviewer.py
    demo_jd = {
        "required_skills": ["SQL", "Python", "PRD", "数据分析", "大模型"],
    }
    demo_resume = {
        "internships": [
            {
                "raw_text": "2025.06-2025.09 AI产品实习，负责需求分析和PRD撰写，推动上线并优化转化。",
            }
        ],
        "projects": [],
        "skills": ["SQL", "Python", "PRD", "数据分析"],
    }
    demo_scores = {
        "教育背景匹配度": 4,
        "相关经历匹配度": 4,
        "技能匹配度": 4,
        "表达完整度": 4,
        "综合推荐度": 4,
    }
    demo_risk = {
        "risk_level": "medium",
        "risk_points": ["相关经历匹配度一般，建议在面试中核验岗位相关职责。"],
    }

    from pprint import pprint

    pprint(build_interview_plan(demo_jd, demo_resume, demo_scores, demo_risk, "建议人工复核"))
