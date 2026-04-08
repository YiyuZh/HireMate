"""Resume Intelligence helpers for structured candidate profile."""

from __future__ import annotations

import re
from typing import Any


_ROLE_FAMILY_KEYWORDS = {
    "ai_pm": ["ai产品", "大模型", "prompt", "rag", "agent", "llm", "aigc"],
    "general_pm": ["产品经理", "prd", "原型", "需求分析", "竞品"],
    "data_analyst": ["数据分析", "sql", "指标", "报表", "数据", "ab测试", "a/b"],
    "user_research": ["用户研究", "访谈", "问卷", "可用性", "调研"],
}


def _guess_role_family(text: str) -> str:
    blob = (text or "").lower()
    best = "unknown"
    best_hits = 0
    for role, keywords in _ROLE_FAMILY_KEYWORDS.items():
        hits = sum(1 for k in keywords if k in blob)
        if hits > best_hits:
            best_hits = hits
            best = role
    return best


def _guess_seniority(text: str) -> str:
    blob = (text or "").lower()
    if any(k in blob for k in ["负责人", "主导", "owner", "lead"]):
        return "mid"
    if any(k in blob for k in ["实习", "intern"]):
        return "intern"
    return "junior"


def _extract_method_output_result_signals(text: str) -> dict[str, list[str]]:
    method = ["需求分析", "PRD", "原型", "SQL", "Python", "访谈", "问卷", "指标", "A/B"]
    output = ["文档", "报告", "方案", "原型", "策略", "看板"]
    result = ["提升", "增长", "优化", "转化", "效率", "结论", "复盘"]
    blob = text or ""
    return {
        "method": [k for k in method if k in blob],
        "output": [k for k in output if k in blob],
        "result": [k for k in result if k in blob],
    }


def build_candidate_profile(
    parsed_resume: dict[str, Any],
    normalized_text: str = "",
    raw_text: str = "",
) -> dict[str, Any]:
    text_blob = "\n".join(
        [
            str(parsed_resume.get("education") or ""),
            str(parsed_resume.get("degree") or ""),
            str(parsed_resume.get("major") or ""),
            str(parsed_resume.get("graduation_date") or ""),
            str(parsed_resume.get("skills") or ""),
            normalized_text or raw_text or "",
        ]
    )

    internships = parsed_resume.get("internships") or []
    projects = parsed_resume.get("projects") or []
    experience_blob = " ".join([str(item.get("raw_text") or "") for item in internships + projects if isinstance(item, dict)])
    timeline_risks = []
    if not re.search(r"(19|20)\d{2}[./-]\d{1,2}", experience_blob):
        timeline_risks.append("经历时间线不清晰")

    missing = []
    if not parsed_resume.get("education"):
        missing.append("教育信息缺失")
    if not (internships or projects):
        missing.append("实习/项目经历缺失")
    if not parsed_resume.get("skills"):
        missing.append("技能清单缺失")

    signals = _extract_method_output_result_signals(experience_blob)

    profile = {
        "education_summary": str(parsed_resume.get("education") or ""),
        "internship_summary": [str(item.get("raw_text") or "") for item in internships[:3] if isinstance(item, dict)],
        "project_summary": [str(item.get("raw_text") or "") for item in projects[:3] if isinstance(item, dict)],
        "skill_inventory": parsed_resume.get("skills") or [],
        "role_family_guess": _guess_role_family(text_blob),
        "seniority_guess": _guess_seniority(text_blob),
        "method_output_result_signals": signals,
        "timeline_risks": timeline_risks,
        "missing_info_points": missing,
    }
    return profile
