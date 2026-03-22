"""初筛编排模块（结论规则增强版）。

模块职责：
- 串联 JD 解析、简历解析、评分、风险分析和面试建议。
- 根据五维评分 + 风险等级规则输出最终初筛结论。
- 输出结构化结果，供 app.py 渲染与后续扩展。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.ai_reviewer import run_ai_reviewer
from src.interviewer import build_interview_plan
from src.jd_parser import parse_jd
from src.resume_parser import parse_resume
from src.role_profiles import DEFAULT_SCREENING_THRESHOLDS, detect_role_profile, get_profile_by_name
from src.risk_analyzer import analyze_risk
from src.scorer import score_candidate, to_score_values


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

    # 3) 结论理由（2-4条）
    reasons: list[str] = [
        f"综合推荐度为 {overall_score}/5（岗位阈值：>= {pass_line} 推荐，< {review_line} 暂不推荐，其余建议复核）。",
        f"关键门槛：相关经历={exp_score}/{min_exp}、技能={skill_score}/{min_skill}、表达完整度={expression_score}/{min_expression}。",
    ]

    if normalized_risk:
        reasons.append(f"风险等级修正：{normalized_risk}，基础结论“{base_result}”调整为“{final_result}”。")
    else:
        reasons.append("当前未提供明确风险等级，按评分规则直接给出结论。")

    if len(reasons) > 4:
        reasons = reasons[:4]

    return {
        "screening_result": final_result,
        "screening_reasons": reasons,
        "gating_signals": gating_signals,
    }




def _collect_evidence_snippets(parsed_resume: dict[str, Any], limit: int = 5) -> list[dict[str, str]]:
    snippets: list[dict[str, str]] = []
    for frag in (parsed_resume.get("internships") or []) + (parsed_resume.get("projects") or []):
        raw = str(frag.get("raw_text") or "").strip()
        if raw:
            snippets.append({"source": "经历", "text": raw[:120]})
        if len(snippets) >= limit:
            return snippets
    edu = str(parsed_resume.get("education") or "").strip()
    if edu and len(snippets) < limit:
        snippets.append({"source": "教育", "text": edu[:120]})
    return snippets


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
    evidence_snippets = _collect_evidence_snippets(parsed_resume)
    ai_review_suggestion = run_ai_reviewer(
        parsed_jd=parsed_jd,
        parsed_resume=parsed_resume,
        role_profile=role_profile,
        scoring_config=scoring_cfg,
        score_details=score_details,
        risk_result=risk_result,
        screening_result=decision_bundle,
        evidence_snippets=evidence_snippets,
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
